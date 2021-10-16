#!/usr/bin/env python3
import argparse
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
import configparser
import datetime
import dateutil.parser
import json
import logging
import multiprocessing
import re
import requests
import signal
import sys
from typing import Tuple
from paho.mqtt import publish as mqtt_publish

config = configparser.ConfigParser()

l = logging.getLogger(__name__)
lh = logging.StreamHandler(sys.stdout)
lh.setFormatter(logging.Formatter("[%(levelname)s]: %(message)s"))
l.addHandler(lh)


s = BlockingScheduler()

manager = multiprocessing.Manager()
versions = manager.dict()


def signal_handler(sig, frame):
    l.info("shutting down")
    s.shutdown()
    sys.exit(0)


def mqtt_send(topic: str, msg: str) -> None:

    username = config.get('mqtt', 'username', fallback=None)
    password = config.get('mqtt', 'password', fallback=None)

    auth = {
        'username': username,
        'password': password,
    } if username is not None and password is not None else None

    tls = {
        'ca_certs': config.get('mqtt', 'ca_certs', fallback='/etc/ssl/certs/ca-certificates.crt'),
        'insecure': not config.getboolean('mqtt', 'tls_verify', fallback=True)
    } if config.getboolean('mqtt', 'tls', fallback=False) else None

    mqtt_publish.single(
        topic=topic,
        payload=msg,
        hostname=config.get('mqtt', 'host', fallback='localhost'),
        port=config.getint('mqtt', 'port', fallback=1883),
        tls=tls,
        auth=auth,
    )


def event_is_in_future(event: dict) -> bool:
    date = dateutil.parser.isoparse(event['date'])
    now = datetime.datetime.now(date.tzinfo)

    return date > now


def fetch_schedule_json(url: str) -> dict:
    """
    Fetches the schedule as JSON from the provided url and returns it as a dict.
    """
    ua = {
        'User-Agent': 'fahrplan durchsage (github.com/chaoswest-tv/durchsage)',
    }
    try:
        r = requests.get(url, timeout=10, headers=ua)
        r.raise_for_status()
    except (requests.exceptions.HTTPError,
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
            requests.exceptions.RequestException) as e:
        l.error("error fetching the schedule.json: %s" % str(e))
        raise e

    try:
        d = r.json()
    except json.decoder.JSONDecodeError as e:
        l.error("error parsing returned json: %s" % str(e))
        raise e

    return d


def parse_schedule(data: dict) -> Tuple[list, str]:
    """
    Parses the schedule dict to find all future events and the schedule version.
    """
    version = data['schedule']['version']
    events = []

    # extract all events across all days and rooms
    for day in data['schedule']['conference']['days']:
        for room in day['rooms']:
            events.extend(day['rooms'][room])

    # filter out events that are not in the future
    events = list(filter(event_is_in_future, events))

    return events, version


def update_event_jobs(fahrplan: str) -> None:
    """
    Loads the current schedule from the configured url. If the schedule version changes,
    removes all pending event jobs and adds all events from the fetched schedule as new jobs.

    This routine will be periodically executed by a cron trigger.
    """

    l.debug("%s - updating schedule. local state has version '%s'." %
            (fahrplan, versions.get(fahrplan, None)))

    fc = config["fahrplan:%s" % fahrplan]
    pre_announce_mins = fc.getint('pre_announce', fallback=0)

    try:
        d = fetch_schedule_json(fc.get('url'))
        events, version = parse_schedule(d)
    except Exception as _:
        l.error("%s - updating the schedule failed." % fahrplan)
        return

    # if the version did not change we don't need to do anything.
    if versions.get(fahrplan, None) == version:
        return

    # remove all old scheduled events
    for job in [x for x in s.get_jobs() if x.name.startswith("%s_event_" % fahrplan)]:
        job.remove()

    # add all new events to scheduler
    for event in events:
        date = dateutil.parser.isoparse(event['date'])
        s.add_job(announce_event, args=[fahrplan, event], trigger='date',
                  run_date=date, name="%s_event_%s" % (fahrplan, event['guid']))
        l.debug("announcing %s at %s" % (event['guid'], date))

        if pre_announce_mins != 0:
            pre_date = date - datetime.timedelta(minutes=pre_announce_mins)
            s.add_job(announce_event, args=[fahrplan, event, True], trigger='date',
                      run_date=pre_date, name="%s_event_pre_%s" % (fahrplan, event['guid']))
            l.debug("pre-announcing %s at %s" % (event['guid'], pre_date))

    versions[fahrplan] = version
    l.info("%s - imported schedule version '%s' with %d upcoming events." %
           (fahrplan, versions[fahrplan], len(events)))
    mqtt_send('fahrplan/%s/version' % fahrplan, versions[fahrplan])


def announce_event(fahrplan: str, event: dict, pre: bool = False) -> None:
    """
    Takes an event dict and announces it by sending it onto the configured MQTT topic.
    """
    l.info("%s - announcing event %s" % (fahrplan, event['guid']))

    sub_topic = 'soon' if pre else 'now'
    msg = json.dumps(event)
    mqtt_send("fahrplan/%s/%s" % (fahrplan, sub_topic), msg)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)

    parser = argparse.ArgumentParser(description='fahrplan durchsage daemon')
    parser.add_argument('-c', '--config', help='path to config file',
                        type=str, default='config.ini', required=False)
    parser.add_argument(
        '-v', '--verbose', help='increase verbosity level', action='count', default=0)
    args = parser.parse_args()

    # set logging verbosity
    loglevel = max(logging.WARNING - (10 * args.verbose), 10)
    l.setLevel(loglevel)

    # import configuration file
    config.read(args.config)

    # find all fahrplan sections from configfile.
    sections = [section.lstrip('fahrplan:') for section in config.sections(
    ) if section.startswith('fahrplan:')]

    for fahrplan in sections:
        fc = config["fahrplan:%s" % fahrplan]
        # names need to start with letter or number, and can contain letters, numbers, dashes - or underscores _
        if re.fullmatch('^[a-z0-9][a-z0-9\-\_]*$', fahrplan) is None:
            l.error(
                "config section [fahrplan:%s] has invalid name. will skip section." % fahrplan)
            continue

        if fc.get('url', fallback=None) is None:
            l.error(
                "config section [fahrplan:%s] is missing 'url'. will skip section." % fahrplan)
            continue

        cron = CronTrigger.from_crontab(fc.get('cron', fallback='*/5 * * * *'))
        s.add_job(update_event_jobs, args=[
                  fahrplan], name="update_event_jobs_%s" % fahrplan, trigger=cron)
        update_event_jobs(fahrplan)

    # hand over to scheduler, this will block until scheduler is shut down
    s.start()
