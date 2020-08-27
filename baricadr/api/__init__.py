import os

from baricadr.db_models import PullTask
from baricadr.extensions import db

from celery.result import AsyncResult

from email_validator import EmailNotValidError, validate_email

from flask import (Blueprint, current_app, jsonify, request)


api = Blueprint('api', __name__, url_prefix='/')

# Endpoint to check if API is running for CLI tests
# Might return endpoints, version, anything
@api.route('/', methods=['GET'])
def home():
    return jsonify({"msg":"Hello world"})

@api.route('/pull', methods=['POST'])
def pull_files():
    current_app.logger.debug("API call: Pulling %s" % request.json)

    if 'path' not in request.json:
        return jsonify({'error': 'Missing "path"'}), 400

    # Normalize path
    asked_path = os.path.abspath(request.json['path'])

    email = None
    if 'email' in request.json:
        email = request.json['email']

        try:
            v = validate_email(email)
            email = v["email"]
        except EmailNotValidError as e:
            return jsonify({'error': str(e)}), 400

    # Check if we're already pulling the file
    pulling_task_id = current_app.repos.is_already_pulling(asked_path)
    if pulling_task_id:
        current_app.logger.info("Already pulling '%s' in task '%s', no new task." % (asked_path, pulling_task_id))
        task_id = pulling_task_id
    else:
        locking_task_id = current_app.repos.is_locked_by_subdir(asked_path)

        task = current_app.celery.send_task('pull_file', (asked_path, email, locking_task_id))
        task_id = task.task_id
        current_app.logger.info("Created pull task %s" % task_id)

        # Save a reference to this task in db
        pt = PullTask(path=asked_path, task_id=task_id)
        db.session.add(pt)
        db.session.commit()

    return jsonify({'task': task_id})

@api.route('/get_files', methods=['POST'])
def get_files():
    current_app.logger.debug("API call: Listing %s" % request.json)

    if not request.json or 'path' not in request.json:
        return jsonify({'error': 'Missing "path"'}), 400

    compare = False

    if 'compare' in request.json and str(request.json['compare']).lower() == "true":
        compare = True

    max_depth = 1
    if 'max_depth' in request.json:
        max_depth = request.json['max_depth']

    asked_path = os.path.abspath(request.json['path'])
    repo = current_app.repos.get_repo(asked_path)
    files = repo.remote_list(asked_path, compare=compare, max_depth=max_depth)

    return jsonify(files)


@api.route('/status/<task_id>', methods=['GET'])
def status_pull(task_id):
    current_app.logger.info("API call: Getting status of task %s" % task_id)
    res = AsyncResult(task_id)
    info = res.info
    error = 'false'

    # Make exceptions readable
    if isinstance(info, RuntimeError) or isinstance(info, TypeError):
        error = 'true'
        info = str(info)

    current_app.logger.debug("Task state from Celery: %s" % res.info)
    return jsonify({'finished': str(res.ready()).lower(), 'error': error, 'info': info})


@api.route('/zombie', methods=['GET'])
def zombie():
    current_app.logger.info("API call: Killing zombies")
    task = current_app.celery.send_task('cleanup_zombie_tasks')
    task_id = task.task_id
    return jsonify({'task': task_id})
