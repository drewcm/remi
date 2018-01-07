#!/usr/bin/env python
import json
import os
import time
import uuid
import logging

import pika
import settings

class ResponseTimeout(Exception):
    """Timeout exception while waiting for a response from ReminderManager"""
    pass

class ReminderRpcClient(object):
    def __init__(self):
        self.response = None
        self.logger = logging.getLogger(__name__)
        credentials = pika.PlainCredentials(
            os.environ.get("RABBITMQ_DEFAULT_USER"),
            os.environ.get("RABBITMQ_DEFAULT_PASS"))
        self.pika_conn = pika.BlockingConnection(
            pika.ConnectionParameters(host=settings.cfg["rabbitmq"]["host"],
                                      port=settings.cfg["rabbitmq"]["port"],
                                      credentials=credentials))
        self.channel = self.pika_conn.channel()
        result = self.channel.queue_declare(exclusive=True)
        self.callback_queue = result.method.queue
        self.channel.basic_consume(self.on_response, no_ack=True,
                                   queue=self.callback_queue)

    def on_response(self, dummy_ch, dummy_method, props, body):
        if self.correlation_id == props.correlation_id:
            self.response = json.loads(body.decode('utf-8'))

    def call(self, message, timeout=4):
        self.response = None
        self.correlation_id = str(uuid.uuid4())
        self.logger.info("Sending reminder request on '%s'",
            settings.cfg["rabbitmq"]["queue_in"])
        self.channel.basic_publish(exchange='',
                                   routing_key=settings.cfg["rabbitmq"]["queue_in"],
                                   properties=pika.BasicProperties(
                                         reply_to = self.callback_queue,
                                         correlation_id = self.correlation_id,
                                         expiration=str(timeout * 1000),
                                         ),
                                   body=json.dumps(message))
        start_time = time.time()
        while self.response is None:
            if (start_time + timeout) < time.time():
                raise ResponseTimeout(
                    "Error: No response received for reminder request on queue "
                    "'{0}'".format(settings.cfg["rabbitmq"]["queue_in"]))
            self.pika_conn.process_data_events()
        return self.response
