from flask import *
import database
from auth import *
from teachers import teachers
from google_auth import validate_token_for_info

PERIODS = list(map(chr, range(65, 72)))


def empty(string):
    return str(string).strip() == ''


def missing_form_field(key):
    return key not in request.form or empty(request.form[key])


def error(code, message):
    return render_template('error.html', code=code, error=message), code


def json_success(message):
    return jsonify({'status': 'success', 'message': str(message)})


def json_failure(code, message):
    return jsonify({'status': 'failure', 'message': str(message)}), code


def error_not_logged_in():
    flash('You must be logged in to view this page.', 'error')
    return redirect('/login?redirect=' + escape(request.path))


def render_login_optional(template):
    return render_template(template, logged_in=logged_handle() is not None, handle=logged_handle())


app = Flask(__name__)
set_secret_key(app)


@app.route('/')
def front_page():
    return render_login_optional('index.html')


@app.route('/about')
def show_about():
    return render_login_optional('about.html')


@app.route('/schedule')
def show_schedule():
    handle = logged_handle()
    if handle is None:
        return error_not_logged_in()

    schedule = database.user_schedule(handle)

    if schedule is None and 'name' not in session:
        flash('You need to log in again', 'error')
        return redirect('/login')

    return render_template('add.html',
                           handle=handle,
                           name=session.get('name', database.user_name(handle)),
                           schedule=schedule,
                           teachers=teachers)


# Relaying final redirect (after log-in) from originator of /login GET to /login POST
# 1. Originator sends user to /login?redirect=<desired_url>
# 2. login.html is rendered with redirect (template variable) set to the value above, or '' if there wasn't one
# 3. Form submission from login.html sends redirect as form key in POST form to /login
# 4. /login POST logic acknowledges URL and redirects the user to it upon success
@app.route('/login', methods=['GET', 'POST'])
def do_login():
    if request.method == 'GET':
        if logged_handle() is not None:
            flash('You are already logged in')
            return redirect(request.args.get('redirect', '/schedule'))
        return render_template('login.html', redirect=request.args.get('redirect', ''))

    # expected form argument: id_token, redirect (optional)
    if missing_form_field('id_token'):
        return error(400, 'Missing token from Google sign-in')

    try:
        handle, name = validate_token_for_info(request.form['id_token'])
        log_in(handle)

        flash('Logged in successfully as ' + name)
        default = '/schedule'

        if not database.handle_exists(handle):
            session['name'] = name

        if missing_form_field('redirect'):
            return redirect(default)
        return redirect(request.form.get('redirect', default))

    except ValueError as e:
        return error(400, 'Authentication failed: ' + str(e))


@app.route('/logout')
def do_logout():
    log_out()

    flash('You were successfully logged out')
    return redirect('/')


@app.route('/user/<handle>', methods=['GET'])
def show_user(handle):
    if logged_handle() is None:
        return error_not_logged_in()

    if not database.handle_exists(handle):
        return error(404, 'No schedule exists for ' + str(handle))

    schedule = database.user_schedule(handle)
    name = database.user_name(handle)
    return render_template('user.html', schedule=schedule, name=name, handle=handle, teachers=teachers)


@app.route('/update', methods=['POST'])
def do_update():
    # Redirect path reading from args
    redirect_path = request.args.get('from', '/schedule')
    if empty(redirect_path):
        redirect_path = '/schedule'

    # Form key existence check
    handle = logged_handle()
    if handle is None:
        flash('You must be logged in to make updates to schedules', 'error')
        return redirect('/')

    if any(map(missing_form_field, PERIODS)):
        return error(400, 'One or more periods missing')

    # Teacher validity check
    new_schedule = list(map(request.form.get, PERIODS))
    for teacher in new_schedule:
        if teacher not in teachers:
            return error(400, 'Unknown teacher: ' + teacher)
    try:
        if database.handle_exists(handle):
            database.update_schedule(handle, new_schedule)
            flash('Schedule updated successfully')
        else:
            if 'name' not in session:
                return error(417, 'Name unknown because it is not in user session')

            database.add_schedule(handle, session.pop('name'), new_schedule)
            flash('Schedule added successfully')

        return redirect(redirect_path)
    except Exception as e:
        return error(500, str(e))


@app.route('/roster/<period>/<teacher>')
def show_roster(period, teacher):
    if logged_handle() is None:
        return error_not_logged_in()

    if period not in PERIODS:
        return error(400, 'Invalid period: ' + period)
    if teacher not in teachers:
        return error(400, 'Invalid teacher: ' + teacher)

    roster = database.class_roster(period, teacher)
    if len(roster) == 0:
        flash('That class is either empty or nonexistent', 'error')
        return redirect('/schedule')

    return render_template('roster.html', period=period, teacher=teacher, roster=roster, handle=logged_handle())


@app.route('/search')
def do_search():
    if logged_handle() is None:
        return error_not_logged_in()

    query = request.args.get('query')
    if query is None:
        return render_template('search-new.html')

    results = database.search_user(str(query))
    return render_template('search-result.html', results=results)


@app.teardown_appcontext
def close_connection(_):
    db = getattr(g, '_database', None)
    if db is not None:
        db.commit()
        db.close()


if __name__ == "__main__":
    app.run()
