#!/usr/bin/env python

import json
import logging
import logging.config
import os
import re
import time
import threading

import pika
from pushbullet import Listener, Pushbullet

import settings
from reminder_rpc_client import ReminderRpcClient, ResponseTimeout

HTTP_PROXY_HOST = None
HTTP_PROXY_PORT = None

class ReminderMessageConsumer(threading.Thread):
    """Consume reminder messages off of ReminderMQ queue, and send out via PushBullet.

    Note:
       Requires that the environment variable PUSHBULLET_API_TOKEN is set.
    """

    def __init__(self):
        threading.Thread.__init__(self)
        self.logger = logging.getLogger(__name__)
        self.logger.info("Starting ReminderMessageConsumer...")
        self.pb = Pushbullet(os.environ.get("PUSHBULLET_API_TOKEN"))
        self.is_interrupted = False

    def stop(self):
        """Tells this thread to wrap up."""
        self.is_interrupted = True

    def run(self):
        """Wrapper around _run to catch and log exceptions"""
        while True:
            try:
                self._run()
            except pika.exceptions.ConnectionClosed:
                self.logger.exception("Lost connection to RabbitMQ.  Restarting...")
            except Exception as ex:
                self.logger.exception(str(ex))
            self.logger.info("Sleeping")
            time.sleep(0.5)

    def _run(self):
        """Connect to RabbitMQ and pass along any reminder messages.

        Note:
            Requires that RABBITMQ_DEFAULT_USER and RABBITMQ_DEFAULT_PASS
            environment variables are set.
        """
        credentials = pika.PlainCredentials(
            os.environ.get("RABBITMQ_DEFAULT_USER"),
            os.environ.get("RABBITMQ_DEFAULT_PASS"))
        pika_conn = pika.BlockingConnection(
            pika.ConnectionParameters(
                host=settings.cfg["rabbitmq"]["host"],
                port=settings.cfg["rabbitmq"]["port"],
                credentials=credentials
                ))
        self.logger.info("Outgoing reminders will be sent on queue '%s'",
            settings.cfg["rabbitmq"]["queue_out"])
        channel = pika_conn.channel()
        channel.queue_declare(queue=settings.cfg["rabbitmq"]["queue_out"])
        #channel.basic_consume(self.process_message,
        #    queue=settings.cfg["rabbitmq"]["queue_out"], no_ack=True)
        #channel.start_consuming()
        # https://stackoverflow.com/a/46493240/9027089
        for message in channel.consume(settings.cfg["rabbitmq"]["queue_out"],
                                       inactivity_timeout=1):
            #self.logger.info("CONSUME")
            if self.is_interrupted:
                channel.cancel()
                break
            if not message:
                continue
            method, _, body = message
            channel.basic_ack(method.delivery_tag)
            body_json = json.loads(body.decode('utf-8'))
            self.logger.info("Received %s", str(body_json))
            self.pb.push_note("", (body_json["data"]["text"]))
        channel.close()
        pika_conn.close()
        self.logger.info("ReminderMessageConsumer.run() exiting.")

class PushbulletManager():
    """Manager interface to and from PushBullet.

    Note:
       Requires that the environment variable PUSHBULLET_API_TOKEN is set.
    """
    REMINDER_KEYWORD = r"remi(nder)?\s+"
    last_mod_time = 0.0

    def __init__(self):
        """Create PushBullet listener."""
        self.logger = logging.getLogger(__name__)
        self.logger.info("Starting PushbulletManager...")
        self.pb = Pushbullet(os.environ.get("PUSHBULLET_API_TOKEN"))
        push_list = self.pb.get_pushes(limit=1)
        if push_list:
            self.last_mod_time = push_list[0]["modified"]
        self.pb_listener = Listener(account=self.pb,
                                    on_push=self.pb_on_push,
                                    on_error=self.pb_on_error,
                                    http_proxy_host=HTTP_PROXY_HOST,
                                    http_proxy_port=HTTP_PROXY_PORT)
        self.rmc = ReminderMessageConsumer()
        self.rmc.start()

    def run(self):
        """Listen for messages on PushBullet websocket."""
        try:
            self.logger.info('Waiting for messages. To exit press CTRL+C')
            self.pb_listener.run_forever()
        except KeyboardInterrupt:
            self.rmc.stop()
            self.rmc.join()
            raise
        finally:
            self.pb_listener.close()

    def pb_on_error(self, websocket, exception):
        """Error handler for the PushBullet listener.

        Re-raise any exception that occurs, as otherwise it will be swallowed
        up by the PushBullet Listener.
        """
        # If a KeyboardInterrupt is raised during
        # self.pb_listener.run_forever(), this method is invoked, which is the
        # only way we'll know about it.
        raise exception

    def pb_on_push(self, data):
        """Push handler for the PushBullet listener"""
        try:
            self.logger.debug("Received tickle: %s", str(data))
            push_list = self.pb.get_pushes(limit=1,
                modified_after=self.last_mod_time)
            if not push_list:
                self.logger.debug("Ignoring tickle")
                return
            latest = push_list[0]
            self.last_mod_time = latest["modified"]
            self.logger.info("Latest Push: %s", str(latest))
            self.process_push(latest)
        except Exception:
            self.logger.exception("Error processing push message")

    def process_push(self, push):
        """Process PushBullet JSON message.

        Passes the message to the ReminderManager if appropriate
        """
        # Pushbullet can also send Links and Images, which don't have a "body"
        if "body" not in push:
            self.logger.info("Ignoring non-note push: id=%s type=%s",
                push["iden"], push["type"])
            return
        if push["dismissed"]:
            self.logger.debug("Ignoring dismissed push: id=%s, body=%s",
                push["iden"], push["body"])
            return
        # We require the reminder keyword at beginning of message, to avoid
        # an infinite loop of pushbullets.
        if not bool(re.match(self.REMINDER_KEYWORD, push["body"], re.I)):
            self.logger.info("Ignoring non-reminder push: id=%s, body=%s",
                push["iden"], push["body"])
            return
        self.logger.info('Received push: id=%s, body=%s',
            push["iden"], push["body"])
        # Strip off reminder keyword first
        body = re.sub(r'^\S+\s+', '', push["body"])
        request = {"body": body, "source": "pushbullet"}
        self.logger.info("Sending request to reminder RPC service")
        try:
            reminder_rpc = ReminderRpcClient()
            response = reminder_rpc.call(request)
            self.logger.info("Reminder RPC response: %s", str(response))
        except ResponseTimeout as rto:
            self.logger.exception(str(rto))
            response = {"error": {"code": 0, "text": "Unable to connect to server"}}
        if "error" in response:
            self.logger.error("Reminder RPC returned error code '%d' (%s)",
                response["error"]["code"], response["error"]["text"])
            self.pb.push_note("Error Setting Reminder", response["error"]["text"])
        else:
            self.logger.info("Received response: %s", str(response))
            self.logger.info("Dismissing push: id=%s", push["iden"])
            self.pb.dismiss_push(push["iden"])
            self.logger.info("Sending response: %s", response["data"]["text"])
            self.pb.push_note("Got it!", response["data"]["text"])

def main():
    """Set up logging and start up a PushBulletManager object"""
    if "logging" in settings.cfg:
        logging.config.dictConfig(settings.cfg["logging"])
    logger = logging.getLogger(__name__)
    while True:
        try:
            pb_mgr = PushbulletManager()
            pb_mgr.run()
        except pika.exceptions.ConnectionClosed as cc:
            logger.error("Lost connection to RabbitMQ.  Restarting...")
            logger.error(str(cc))
        #except Exception as ex:
        #    logger.exception(str(ex))
        #except (KeyboardInterrupt, SystemExit):
        #    break
        logger.info("Sleeping")
        time.sleep(0.5)


if __name__ == '__main__':
    main()
