# durchsage

Daemon that will follow a pretalx conference schedule.json ("fahrplan") and make announcements ("durchsage") on a MQTT topic when an event starts. 

## architecture

The whole daemon is based around a blocking scheduler based on APscheduler. When launched, it will set up a `update_event_jobs` cron schedule per configured fahrplan.

When this cron is invoked, it will fetch the current fahrplan from the source url, and filter out all events/talks in the past. For each remaining event a new `announce_event` job will be generated that sends out the job data to an MQTT topic exactly when the event starts.

It will use these MQTT topics:

* `fahrplan/<fahrplan>/version`: When a schedule with a different version has been imported, a message containing the version string will be published here.
* `fahrplan/<fahrplan>/now`: When an event begins the whole event data structure is dumped in JSON and published here.
* `fahrplan/<fahrplan>/soon`: (Optional) Just before an event begins the whole event data structure is dumped in JSON and published here. You can configure how many minutes before the actual event start this will happen, or not at all.

## configuration

Create a `config.ini` and either drop it in the same folder as `durchsage.py` or pass the path with a `--config` flag.

There can be only one `mqtt` section defining the connection parameters to the broker.

There can be many `fahrplan:<fahrplan>` sections, where `<fahrplan>` is a user definable name. This will be used internally and for the MQTT topic selection.

`<fahrplan>` needs to begin with a lowercase letter or digit, and can contain lowercase letters, digits, dashes or underscores after the first char.

```INI
[mqtt]
host = 127.0.0.1
port = 8883

tls = yes
tls_verify = yes

username = hans
password = wurst

[fahrplan:example]
# the url is required.
url = https://pretalx.example.com/example-conference/schedule/export/schedule.json

# optional: refresh/pull the fahrplan according to this crontab expression
# defaults to every 5 minutes.
cron = * * * * *

# optional: give an advance notice x minutes before the talk starts on the 'soon' topic
# defaults to no advance notice
pre_announce = 1

[fahrplan:anotherconf]
url = ...

```