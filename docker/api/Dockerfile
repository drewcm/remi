FROM tiangolo/uwsgi-nginx-flask:python3.6

COPY ./remi /app
COPY ./conf/config.json /

RUN apt-get update && apt-get install -y python-pip python-dev build-essential
ADD requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt

ENV PYTHONPATH $PYTHONPATH:/app/
ENV REMI_CONFIG /config.json

