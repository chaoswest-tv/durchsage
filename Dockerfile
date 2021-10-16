FROM python:3.9-alpine
ENV TZ=UTC
WORKDIR /app
ADD requirements.txt .
RUN apk add --no-cache tzdata && pip install -r requirements.txt 
ADD durchsage.py .
CMD python durchsage.py