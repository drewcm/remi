# Remi

A containerized reminder microservice. Reminders can be set up at the command
line or via http//pushbullet.com.  Remi sends the reminder notifications via
PushBullet.  Remi is useful for setting quick reminders that can be received 
on both mobile or non-mobile devices.

Remi is built using Python, Flask, RabbitMQ, SQLite, and Docker.

### Example Usage

The examples below will all set reminders.  The syntax for setting reminders is
the same for the command line as it is when using PushBullet.

```sh
reminder 4h MESSAGE
reminder 1d4hr30m MESSAGE
reminder tomorrow MESSAGE
reminder 7pm MESSAGE
reminder 12:30am MESSAGE
```

### Reminder Confirmation

When a reminder is successfully set, the output will look like the following.

```
Your reminder will be sent tomorrow at 9:00 AM
```

### Deployment

Deploying remi involves setting ``$ENV_FILE`` and running docker-compose:

```sh
export ENV_FILE=$PWD/remi.env && docker-compose up
```

Below is the structure of ``$ENV_FILE``:

```
REMI_CONFIG=/config.json
FLASK_SECRET_KEY=[SET ME]
PUSHBULLET_API_TOKEN=[SET ME]
REST_API_TOKEN=[SET ME]
RABBITMQ_DEFAULT_USER=[SET ME]
RABBITMQ_DEFAULT_PASS=[SET ME]
```

Documentation for getting a ``PUSHBULLET_API_TOKEN`` is [here](https://docs.pushbullet.com/v1/).
``REST_API_TOKEN`` is set by you.  You can use command line tools like ``gpg`` or
``openssl`` to generate a unique API token.

## Author

* **Andrew C. Martin** - [drewcm](https://github.com/drewcm)

## License

This project is licensed under the MIT License - see the [LICENSE.md](LICENSE.md) file for details

