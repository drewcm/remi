FROM python:3

WORKDIR /app

COPY ./remi /app
COPY ./conf/config.json /

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

ENV PYTHONPATH $PYTHONPATH:/app/
ENV REMI_CONFIG /config.json

CMD [ "python", "./pushbullet_interface.py" ]

