import requests
from flask import *

import database
import discord
from auth import *
from config import *
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


def render_login_optional(template, **kwargs):
    return render_template(template, logged_in=logged_handle() is not None,
                           handle=logged_handle(), **kwargs)


def error_no_own_schedule():
    flash('You need to enter your schedule first', 'error')
    return redirect('/update')


def error_private(handle):
    flash(f'The user {handle} has decided to make their schedule private')
    return redirect('/')


app = Flask(__name__)
set_secret_key(app)


@app.before_request
def enforce_domain_https():
    if 'appspot' in request.url or ('prematch.org' in request.url and 'http://' in request.url):
        return redirect('https://prematch.org', code=301)


@app.route('/')
def front_page():
    return render_login_optional('index.html',
                                 schedule_count=database.schedule_count())


@app.route('/about')
def show_about():
    return render_login_optional('about.html')


@app.route('/about/discord')
def show_about_discord():
    return render_login_optional('about_discord.html')


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
            return redirect(request.args.get('redirect', '/dashboard'))
        return render_template('login.html',
                               redirect=request.args.get('redirect', ''))

    # expected form argument: id_token, redirect (optional)
    if missing_form_field('id_token'):
        return error(422, 'Missing token from Google sign-in')

    try:
        handle, name = validate_token_for_info(request.form['id_token'])
        log_in(handle)

        if database.missing_some_lunch(handle) and database.handle_exists(handle):
            flash('You are missing some lunch numbers. To enter them, please visit the Update page.')
        else:
            flash('Successfully logged in as ' + name)

        default = '/dashboard'

        if not database.handle_exists(handle):
            session['name'] = name
            default = '/update'

        if missing_form_field('redirect'):
            return redirect(default)
        return redirect(request.form.get('redirect', default))

    except ValueError as e:
        return error(403, 'Authentication failed: ' + str(e))


@app.route('/logout')
def do_logout():
    if logged_handle() is not None:
        flash('You were successfully logged out')

    log_out()
    return redirect('/')


@app.route('/user')
def show_own_user():
    return show_user(logged_handle())


@app.route('/user/<handle>', methods=['GET'])
def show_user(handle):
    own_handle = logged_handle()
    if own_handle is None:
        return error_not_logged_in()

    can_read = database.can_read(own_handle, handle)
    if not can_read:
        return error_private(handle)

    schedule = database.get_row_from_handle(handle)
    if schedule is None:
        if own_handle == handle:
            return error_no_own_schedule()
        return render_template('profile_not_found.html', bad_handle=handle)

    name = database.user_name(handle)
    lunch = database.lunch_numbers(own_handle, handle)
    return render_template('user.html', schedule=schedule, name=name,
                           handle=handle, teachers=teachers,
                           lunch_numbers=lunch, lunch_blocks=lunch.keys())


@app.route('/dashboard')
def show_own_dashboard():
    handle = logged_handle()

    if handle is None:
        return error_not_logged_in()
    if not database.handle_exists(handle):
        return error_no_own_schedule()

    return show_dashboard(handle)


@app.route('/dashboard/<handle>')
def show_dashboard(handle):
    user_handle = logged_handle()
    if user_handle is None:
        return error_not_logged_in()

    can_read = database.can_read(user_handle, handle)
    if not can_read:
        return error_private(handle)

    schedule = database.get_row_from_handle(handle)
    if schedule is None:
        if user_handle == handle:
            return error_no_own_schedule()
        return render_template('profile_not_found.html', bad_handle=handle)

    name = schedule['name']
    rosters = {}
    for period in PERIODS:
        rosters[period] = database.class_roster(period, schedule[period])

    lunch_rosters = {}
    for block in lunch_blocks:
        num = database.lunch_number(block, schedule[block])
        lunch_rosters[block] = database.lunch_roster(block, num) \
            if num is not None else None

    return render_template('dashboard.html',
                           handle=handle, name=name, schedule=schedule,
                           rosters=rosters,
                           lunch_periods=lunch_blocks, lunches=[1, 2, 3, 4],
                           lunch_rosters=lunch_rosters)


@app.route('/update', methods=['GET', 'POST'])
def do_update():
    handle = logged_handle()
    if handle is None:
        return error_not_logged_in()

    if request.method == 'GET':
        schedule = database.get_row_from_handle(handle)

        if schedule is None and 'name' not in session:
            flash('You need to log in again', 'error')
            return redirect('/login')

        return render_template('add.html',
                               handle=handle,
                               name=session.get('name', database.user_name(handle)),
                               schedule=schedule,
                               teachers=teachers,
                               lunch_periods=lunch_blocks, lunches=[1, 2, 3, 4],
                               lunch_numbers=database.lunch_numbers(handle, handle))
    else:
        # Redirect path reading from args
        redirect_path = request.args.get('from', '/dashboard')
        if empty(redirect_path):
            redirect_path = '/dashboard'

        if any(map(missing_form_field, PERIODS)):
            return error(422, 'One or more periods missing')

        # Teacher validity check
        new_schedule = list(map(request.form.get, PERIODS))
        for teacher in new_schedule:
            if teacher not in teachers:
                return error(422, 'Unknown teacher: ' + teacher)

        # Read lunches (optional)
        lunches = {}
        for block in lunch_blocks:
            nbr = lunches[block] = request.form.get(f'lunch{block}')
            if nbr is not None:
                if nbr not in list('1234'):
                    return error(422, f'Invalid lunch number: {nbr}')
                lunches[block] = int(nbr)

        # Schedule publicly accessible?
        make_public = request.form.get('public') is not None

        try:
            if database.handle_exists(handle):
                database.update_schedule(handle, new_schedule, make_public)
                flash('Schedule updated successfully')
            else:
                if 'name' not in session:
                    return error(417, 'Name unknown because it is not in user session')

                database.add_schedule(handle, session.pop('name'), new_schedule, make_public)
                flash('Schedule added successfully')

            database.upsert_lunch(handle, lunches)
            return redirect(redirect_path)
        except Exception as e:
            return error(500, str(e))


@app.route('/roster/<period>/<teacher>')
def show_roster(period, teacher):
    handle = logged_handle()
    if handle is None:
        return error_not_logged_in()

    if period not in PERIODS:
        return error(422, 'Invalid block: ' + period)
    if teacher not in teachers:
        return error(422, 'Invalid teacher: ' + teacher)

    roster = database.class_roster(period, teacher)
    user_in_class = handle in list(map(lambda row: row[1], roster))
    if not user_in_class:
        roster = list(filter(lambda h: database.can_read(handle, h[1]), roster))

    if len(roster) == 0:
        flash('That class is either empty or nonexistent', 'error')
        return redirect('/')

    roster = database.inject_privacy(handle, roster)
    return render_template('roster.html', period=period, teacher=teacher,
                           roster=roster, handle=handle,
                           lunch_number=database.lunch_number(period, teacher),
                           lunch_periods=lunch_blocks,
                           user_in_class=user_in_class)


@app.route('/lunch/<block>/<number>')
def show_lunch(block, number):
    handle = logged_handle()
    if handle is None:
        return error_not_logged_in()

    if block not in lunch_blocks:
        return error(422, f'Invalid lunch block: {block}')
    if number not in list('1234'):
        return error(422, f'Invalid lunch number: {number}')

    number = int(number)
    roster = database.inject_privacy(handle,
                                     database.restrict_roster(
                                         handle, database.lunch_roster(block, number)))

    if len(roster) == 0:
        flash('No applicable classes were found', 'error')
        return redirect('/dashboard')

    return render_template('lunch.html', handle=handle, roster=roster,
                           block=block, number=number)


@app.route('/search')
def do_search():
    handle = logged_handle()
    if handle is None:
        return error_not_logged_in()

    query = request.args.get('query')
    if query is None or query.strip() == '':
        return render_template('search-new.html', handle=handle)

    results = database.search_user(handle, query.strip())
    return render_template('search-result.html', query=query, results=results,
                           handle=handle)


@app.route('/discord')
def discord_entry_point():
    code = request.args.get('code')
    state = request.args.get('state')

    if code is not None and state is not None:
        return redirect(f'/discord/{code}/{state}')

    if code is None:
        flash('No code was supplied from Discord. Not our fault!', 'error')
    if state is None:
        flash('No state was supplied from Discord. Is this request legit?', 'error')

    return redirect('/')


@app.route('/discord/<code>/<state>')
def verify_discord(code, state):
    handle = logged_handle()
    if handle is None:
        return error_not_logged_in()

    if not database.handle_exists(handle):
        flash('You need to enter your schedule before integration', 'error')
        return redirect('/update')

    try:
        access_token = discord.exchange_code(code)
        user_info = discord.get('/users/@me', access_token)
        user_id = user_info.get('id')
        db_name = database.user_name(handle)

        if not discord.state_valid(state, user_id):
            return "Did you use a link from someone else? Please use $$personalize to get a link, just for you."

        if database.get_assoc_discord_id(handle) is None:
            database.record_discord_assoc(handle, user_id)

        return render_template('discord_integration.html', avatar_src=discord.avatar_url(user_info),
                               user_name=user_info['username'], user_discriminator=user_info['discriminator'],
                               handle=handle, name=db_name)

    except requests.exceptions.HTTPError as e:
        print(e)
        return error(401, 'Discord authorization failed!')


@app.route('/lunch/record', methods=['GET', 'POST'])
def admin_record_lunch():
    if logged_handle() is None:
        return error_not_logged_in()

    if not database.is_admin(logged_handle()):
        abort(404)
        return 'Page not found', 404

    if request.method == 'GET':
        return render_template('record_lunch.html',
                               students=database.roster_of_all(),
                               blocks=lunch_blocks,
                               lunch_numbers=list('1234'),
                               message=request.args.get('m'),
                               handle=logged_handle())
    else:
        handles = request.form.get('handles').split(',')
        block = request.form.get('block')
        number = request.form.get('number')

        if None in [handles, block, number] or len(handles) == 0:
            flash('Missing fields')
            return redirect('/lunch/record')

        if not all(map(database.handle_exists, handles)):
            flash(f'Invalid student')
            return redirect('/lunch/record')
        if block not in lunch_blocks:
            flash(f'Invalid block: {block}')
            return redirect('/lunch/record')
        if number not in '1234':
            flash(f'Invalid lunch number: {number}')
            return redirect('/lunch/record')

        unchanged = []

        for handle in handles:
            schedule = database.get_row_from_handle(handle)
            exist_number = database.lunch_number(block, schedule[block])
            if exist_number is not None:
                unchanged.append(handle)
                continue

            database.add_lunch_number(schedule[block], block, number)

        flash(f'{unchanged} unchanged, others new')
        return redirect('/lunch/record')


from apis import rest_api

app.register_blueprint(rest_api)

if __name__ == "__main__":
    app.run()
