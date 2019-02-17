from flask import *

import database
from auth import *
from config import *
from google_auth import *
from user import *

rest_api = Blueprint('rest_api', __name__)


def api_error(code, message, status='error'):
    return jsonify({
        'status': status,
        'code': code,
        'message': message
    }), code


def api_success(**payload):
    data = {
        'status': 'ok',
        'code': 200
    }
    data.update(payload)
    return jsonify(data)


def api_error_unauthorized():
    return api_error(401, 'Unauthorized (not logged in)')


def api_bad_value(problem_key):
    return api_error(422, f'Invalid or missing value for "{problem_key}"')


# teacher: string
# block: string (letter)
# log-in required
@rest_api.route('/api/lunch/number', methods=['GET'])
def api_lunch_get():
    if logged_handle() is None:
        return api_error_unauthorized()

    teacher = request.args.get('teacher')
    block = request.args.get('block')
    semester = request.args.get('semester')

    if teacher not in teachers:
        return api_bad_value('teacher')
    if block not in lunch_blocks:
        return api_bad_value('block')
    if semester not in semesters:
        return api_bad_value('semester')

    number = database.lunch_number(block, int(semester), teacher)

    if number is None:
        return api_error(404, 'No lunch number set', status='empty')
    else:
        return api_success(number=number)


# id_token: string
@rest_api.route('/api/login', methods=['GET'])
def api_login():
    token = request.args.get('id_token')

    if token is None:
        return api_bad_value('id_token')

    try:
        handle, name = validate_ios_token_for_info(token)
        log_in(handle)
        return api_success(handle=handle)

    except ValueError as e:
        return api_error(403, str(e))


# handle: string
# log-in required
@rest_api.route('/api/schedule', methods=['GET'])
def api_schedule():
    if logged_handle() is None:
        return api_error_unauthorized()

    handle = request.args.get('handle')
    if handle is None:
        return api_bad_value('handle')
    if not database.handle_exists(handle):
        return api_error(404, 'Handle not found: ' + handle)
    if not Reader(logged_handle()).can_read(User.from_db(handle)):
        return api_error(403, 'Cannot read private handle: ' + handle)

    schedule = database.get_row_from_handle(handle)
    response = dict(map(lambda blk: (blk, schedule.get(blk)), all_block_keys()))

    return api_success(**response)
