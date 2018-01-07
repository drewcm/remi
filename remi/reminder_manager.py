#!/usr/bin/env python
import json
import logging
import logging.config
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta

import parsedatetime
import pika
import pytz
import settings
from babel.dates import format_datetime, format_time

TIMEDELTA_REGEX = re.compile(
    r'((?P<days>(\d+)?(\.?\d+)?)d(ays?)?)?((?P<hours>(\d+?)(\.?\d+)?)h((ou)?rs?)?)?((?P<minutes>\d+?)m)?((?P<seconds>\d+?)s)?$',
    re.IGNORECASE)

class QueryThread(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)
        self.logger = logging.getLogger(__name__)
        self.logger.info("Starting QueryThread...")
        self.is_interrupted = False

    def stop(self):
        """Tells this thread to wrap up."""
        self.is_interrupted = True

    def db_reminder_check(self, channel):
        """Check database for any reminders that are past due."""
        c = self.db_conn.cursor()
        result = c.execute('''
            SELECT id, source, stop, content FROM reminder
            WHERE active=1 AND stop < DATETIME('now')''')
        for row in result.fetchall():
            self.logger.info(row)
            msg_id, dummy_msg_source, dummy_msg_stop, msg_content = row
            if msg_content:
                msg_body = "reminder: {0}".format(msg_content)
            else:
                msg_body = "This is your reminder!"
            self.logger.info("Sending reminder on queue '%s': %s",
                settings.cfg["rabbitmq"]["queue_out"], msg_body)
            msg = {"data":{"text": msg_body}}
            channel.basic_publish(
                exchange='',
                routing_key=settings.cfg["rabbitmq"]["queue_out"],
                body=json.dumps(msg))
            c.execute("UPDATE reminder SET active=0 WHERE id={0}".format(
                msg_id))
            self.db_conn.commit()

    def run(self):
        credentials = pika.PlainCredentials(
            os.environ.get("RABBITMQ_DEFAULT_USER"),
            os.environ.get("RABBITMQ_DEFAULT_PASS"))
        with pika.BlockingConnection(
                pika.ConnectionParameters(host=settings.cfg["rabbitmq"]["host"],
                                          port=settings.cfg["rabbitmq"]["port"],
                                          credentials=credentials)) as pika_conn:
            channel = pika_conn.channel()
            channel.queue_declare(queue=settings.cfg["rabbitmq"]["queue_out"])
            self.logger.info("Outgoing reminders will be sent on queue '%s'",
                settings.cfg["rabbitmq"]["queue_out"])
            curr_dir = os.path.dirname(__file__)
            db_path = os.path.join(curr_dir, '../instance', settings.cfg["db_filename"])
            self.db_conn = sqlite3.connect(db_path)
            self.logger.info("Starting QueryThread loop...")
            # https://stackoverflow.com/a/35687118
            while not self.is_interrupted:
                self.db_reminder_check(channel)
                pika_conn.sleep(1 - time.monotonic() % 1)
            channel.close()
            self.logger.info("Stopping QueryThread loop...")

class ReminderManager():
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        parsedatetime.debug = True
        parsedatetime.log = self.logger
        self.logger.info("Starting ReminderManager...")
        curr_dir = os.path.dirname(__file__)
        db_path = os.path.join(curr_dir, '../instance', settings.cfg["db_filename"])
        self.db_conn = sqlite3.connect(db_path)
        self.timezone = pytz.timezone(settings.cfg["default_timezone"])

        c = self.db_conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS reminder (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT,
            start TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            stop TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            request TEXT,
            content TEXT, active INTEGER DEFAULT 1)''')
        self.db_conn.commit()
        self.logger.info("Incoming reminders requests will be received on '%s'",
            settings.cfg["rabbitmq"]["queue_in"])
        credentials = pika.PlainCredentials(
            os.environ.get("RABBITMQ_DEFAULT_USER"),
            os.environ.get("RABBITMQ_DEFAULT_PASS"))
        self.pika_conn = pika.BlockingConnection(
            pika.ConnectionParameters(host=settings.cfg["rabbitmq"]["host"],
                                      port=settings.cfg["rabbitmq"]["port"],
                                      credentials=credentials))
        self.channel = self.pika_conn.channel()
        self.channel.queue_declare(queue=settings.cfg["rabbitmq"]["queue_in"])
        self.channel.basic_qos(prefetch_count=1)
        self.channel.basic_consume(self.process_request,
            queue=settings.cfg["rabbitmq"]["queue_in"])
        self.qt = QueryThread()
        self.qt.start()

    def parse_time(self, time_str):
        """
        Construct a datetime.timedelta object from string input.
        Example input: 00:00:00, 0hr0m0s
        """
        parts = TIMEDELTA_REGEX.match(time_str)
        if parts and parts.start() != parts.end():
            parts = parts.groupdict()
            time_params = {}
            for (name, param) in parts.items():
                if param:
                    time_params[name] = float(param)
                tdelta = timedelta(**time_params)
                dt = datetime.now(self.timezone) + tdelta
        else:
            cal = parsedatetime.Calendar()
            dt, parse_status = cal.parseDT(time_str, tzinfo=self.timezone)
            self.logger.debug("parsedatetime returned '%s'", str(dt))
            if parse_status == 0:
                raise ValueError("Error: invalid time format: " + time_str)
        return dt

    def process_request(self, ch, method, props, body):
        response = {}
        try:
            self.logger.debug("Processing request: %s", str(body))
            message = self._process_request(ch, method, props, body)
            response = {"data": {"text": message}}
        except ValueError as ve:
            response = {"error": {"code": 400, "text":
                ("Unable to parse the date/time you provided.  Use the "
                 "\"HELP\" command to see usage examples.")}}
            self.logger.exception("Error processing reminder request")
        except sqlite3.DatabaseError as dbe:
            response = {"error": {"code": 500, "text":
                ("There was an error creating your reminder.  Please try "
                 "again later.")}}
            self.logger.exception("Error processing reminder request")
        except Exception as ex:
            response = {"error": {"code": 500, "text":
                ("An unexpected error occurred while processing your request. "
                 "Please try again.")}}
            self.logger.exception("Error processing reminder request")
        ch.basic_publish(exchange='', routing_key=props.reply_to,
            properties=pika.BasicProperties(correlation_id=props.correlation_id),
            body=json.dumps(response))
        ch.basic_ack(delivery_tag = method.delivery_tag)
        self.logger.info("Sent the following response: %s", str(response))

    @staticmethod
    def make_error_response(code, text, user_message=None):
        """Helper function for formatting error responses

        Args:
            code: error status code
            text: error message string

        Returns:
            Python dictionary for error message.
        """
        return {
            "error": {
                "code": code,
                "text": text
            }}

    def _process_request(self, ch, method, props, body):
        body = json.loads(body.decode('utf-8'))
        self.logger.info("Received message: %s", str(body))
        body_parts = re.split(r"\s+", body["body"], 1)
        time_str = body_parts[0]
        if len(body_parts) == 2:
            reminder = body_parts[1]
        else:
            reminder = ""
        dt = self.parse_time(time_str)
        #self.db_conn = sqlite3.connect("instance/remi.sqlite")
        c = self.db_conn.cursor()
        c.execute('''
            insert into reminder (source, stop, request, content)
            values (?, ?, ?, ?)''',
            [body["source"], dt.astimezone(pytz.utc), body["body"], reminder])
        self.db_conn.commit()
        # send response
        if dt.second == 0:
            fmt = "short"
        else:
            fmt = "medium"
        if dt.date() == datetime.now(self.timezone).date():
            dt_fmt = "today at {0}".format(
                format_time(dt.time(), format=fmt, locale='en_US'))
        elif dt.date() == datetime.now(self.timezone).date() + timedelta(days=1):
            dt_fmt = "tomorrow at {0}".format(
                format_time(dt.time(), format=fmt, locale='en_US'))
        else:
            dt_fmt = "on {0}".format(
                format_datetime(dt, format=fmt, locale='en_US'))
        response = "Your reminder will be sent " + dt_fmt
        self.logger.info('Sending response: "%s"', response)
        return response

    def run(self):
        try:
            self.logger.info('Waiting for messages. To exit press CTRL+C')
            self.channel.start_consuming()
        except KeyboardInterrupt:
            self.qt.stop()
            self.qt.join()
            raise
        finally:
            self.channel.close()
            self.pika_conn.close()

def main():
    """Set up logging and start up a ReminderManager object"""
    if "logging" in settings.cfg:
        logging.config.dictConfig(settings.cfg["logging"])
    logger = logging.getLogger(__name__)
    while True:
        try:
            logger.info("Starting ReminderManager...")
            rm = ReminderManager()
            rm.run()
        except pika.exceptions.ConnectionClosed as cc:
            logger.error("Lost connection to RabbitMQ.  Restarting...")
            logger.error(str(cc))
        time.sleep(1)

if __name__ == '__main__':
    main()
