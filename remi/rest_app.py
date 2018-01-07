#!/usr/bin/env python

import base64
import json
import logging.config
import os

import settings
from flask import Flask, abort, json, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager, UserMixin, login_required
from functools import wraps
from reminder_rpc_client import ReminderRpcClient

#config = pkg_resources.resource_filename('instance', 'remi_config.json')
#with open(config) as json_config_file:
#    settings.cfg = json.load(json_config_file)
app = Flask(__name__)
app.config.update(dict(
    DEBUG=settings.cfg["flask"]["debug"],
    SECRET_KEY=os.environ.get("FLASK_SECRET_KEY"),
    API_TOKEN=os.environ.get("REST_API_TOKEN"),
    #LOGGER_NAME='remi',
))
limiter = Limiter(
    app,
    key_func=get_remote_address,
    default_limits=["10 per minute"],
)
login_manager = LoginManager()
login_manager.init_app(app)

# Required to use the app.logger before loading logging config
app.logger.debug("Initializing logger")
if "logging" in settings.cfg:
    logging.config.dictConfig(settings.cfg["logging"])

# https://flask-login.readthedocs.io/en/latest/#custom-login-using-request-loader
# http://gouthamanbalaraman.com/blog/minimal-flask-login-example.html
@login_manager.request_loader
def load_user_from_request(request):
    """Decorator for performing authentication of requests' API_TOKEN"""
    # first, try to login using the api_key url arg
    #api_key = request.args.get('api_key')
    #if api_key:
    #    user = User.query.filter_by(api_key=api_key).first()
    #    if user:
    #        return user
    # next, try to login using Basic Auth
    api_key = request.headers.get('Authorization')
    if api_key:
        api_key = api_key.replace('Basic ', '', 1)
        try:
            api_key = base64.b64decode(api_key)
        except TypeError:
            pass
        api_key = api_key.decode("utf-8")
        api_key, dummy_pass = api_key.split(":")
        if app.config["API_TOKEN"] == api_key:
            return UserMixin()

    # finally, return None if both methods did not login the user
    return None

@app.route('/api/v1/set-reminder', methods=['POST'])
@login_required
def set_reminder():
    """API call for setting a reminder.

    Makes an RPC call to the Reminder Manager over RabbitMQ with the reminder
    request.
    """
    response = {"error": 200}
    if not request.json:
        abort(400)
    req_json = request.get_json()
    msg = {"body": req_json["body"],
           "source": "webservice",
           "remote_addr": request.remote_addr
          }
    reminder_rpc = ReminderRpcClient()
    response = reminder_rpc.call(msg)
    return jsonify(response)

@app.before_request
def log_request_info():
    """Perform debug logging of request."""
    app.logger.info('%s %s %s %s',
              request.remote_addr,
              request.method,
              request.scheme,
              request.full_path)
    app.logger.debug('Headers: %s', request.headers)
    app.logger.debug('Body: %s', request.get_data())

@app.after_request
def after_request(response):
    """After the request, log any requests resulting in an error response."""
    if response.status_code != 200:
        app.logger.error('%s %s %s %s %s',
                  request.remote_addr,
                  request.method,
                  request.scheme,
                  request.full_path,
                  response.status)
        app.logger.error('Headers: %s', request.headers)
        app.logger.error('Body: %s', request.get_data())
    return response

def error_response(code, err):
    """Log error and jsonify error message.

    Args:
        code: http status code
        err: error message string

    Returns:
        Tuple of JSON error response message and status code.
    """
    app.logger.error(str(err))
    response = {
        "error": {
            "code": code,
            "text": "Error: " + str(err),
        }}
    return jsonify(response), code

def get_http_exception_handler(app):
    """Overrides the default http exception handler to return JSON."""
    handle_http_exception = app.handle_http_exception
    @wraps(handle_http_exception)
    def wrap_handle_http_exception(exception):
        exc = handle_http_exception(exception)
        return error_response(exc.code, exc.description)
    return wrap_handle_http_exception

# Override the HTTP exception handler.
app.handle_http_exception = get_http_exception_handler(app)

@app.errorhandler(500)
def internal_server_error(dummy_err):
    """Function handling and logging internal server errors.

    Flask handles internal server errors differently than other errors, so we
    can't use the catch-all handler for them.

    Args:
        dummy_err: Error message. Ignored in favor of a generic response.

    Returns:
        JSON error response.
    """
    return error_response(500,
        "The server encountered an internal error and was unable to complete "
        "your request.")
