from flask import Flask, render_template, request, redirect, session, url_for, jsonify, flash, abort
import boto3
from boto3.dynamodb.conditions import Key, Attr
import os
import uuid
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from functools import wraps
from flask_caching import Cache
import requests
from botocore.exceptions import ClientError

app = Flask(__name__, static_url_path='/static')
app.secret_key = 'dev-secret-key-!23$TravelGo2025'
cache = Cache(app, config={'CACHE_TYPE': 'simple'})

# Initialize AWS resources
dynamodb = boto3.resource('dynamodb', region_name='ap-south-1')
sns_client = boto3.client('sns', region_name='ap-south-1')
SNS_TOPIC_ARN = 'arn:aws:sns:us-east-1:586794467071:Travelgo:90e78453-f4b5-485a-85bd-dc379e38a683'  # Replace with your actual ARN

# Define DynamoDB tables
users_table = dynamodb.Table('users')
wishlist_table = dynamodb.Table('wishlists')
places_table = dynamodb.Table('places')
flights_table = dynamodb.Table('flights_data')
bookings_table = dynamodb.Table('bookings')
bus_bookings_table = dynamodb.Table('bus_bookings')
train_bookings_table = dynamodb.Table('train_bookings')
hotel_bookings_table = dynamodb.Table('hotels_bookings')
trip_plans_table = dynamodb.Table('trip_plans')


# Updated query function to avoid GSI
def query_by_email_scan(table, email):
    response = table.scan(
        FilterExpression=Attr('email').eq(email)
    )
    return sorted(response.get('Items', []), key=lambda x: x.get('booked_at', ''), reverse=True)

# Add default fields to places
default_fields = {
    "best_season": "All year",
    "duration": "Not specified",
    "highlights": "Scenic views, culture, etc.",
    "tags": ["popular", "must-visit"],
    "tips": "Bring comfortable shoes and a camera!"
}

try:
    response = places_table.scan()
    for item in response.get('Items', []):
        place_id = item.get('place_id')
        for field, value in default_fields.items():
            if field not in item:
                places_table.update_item(
                    Key={'place_id': place_id},
                    UpdateExpression=f"SET #f = :v",
                    ExpressionAttributeNames={'#f': field},
                    ExpressionAttributeValues={':v': value}
                )
    print("Default fields added where missing.")
except Exception as e:
    print(f"Error updating places: {e}")

# Add default seat layout to flights
seat_layout = {}
for row in ['A', 'B', 'C', 'D', 'E', 'F']:
    for col in range(1, 5):
        seat_layout[f"{row}{col}"] = "available"

try:
    response = flights_table.scan()
    for flight in response.get('Items', []):
        flight_id = flight.get('flight_id')
        if 'seats' not in flight:
            flights_table.update_item(
                Key={'flight_id': flight_id},
                UpdateExpression="SET seats = :s",
                ExpressionAttributeValues={':s': seat_layout}
            )
    print("Seat layout added to all flights.")
except Exception as e:
    print(f"Error updating flights: {e}")

# ---------------- DECORATORS ----------------
def login_required(f):
    @wraps(f)
    def wrap(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrap

# ---------------- HOME ----------------
@app.route('/')
def home():
    logged_in = 'email' in session
    return render_template('home.html', logged_in=logged_in)

# ---------------- REGISTER ----------------
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form['email']
        username = request.form['username']
        password = request.form['password']
        hashed_password = generate_password_hash(password)

        response = users_table.get_item(Key={'email': email})
        if 'Item' in response:
            return render_template('register.html', error="Email already registered.")

        users_table.put_item(Item={
            'email': email,
            'user_id': str(uuid.uuid4()),
            'username': username,
            'hashed_password': hashed_password,
            'login_count': 0
        })

        try:
            sns_client.publish(
                TopicArn=SNS_TOPIC_ARN,
                Message=f"Welcome {username} to TravelGo! Start planning your adventures now.",
                Subject="Welcome to TravelGo!"
            )
        except ClientError as e:
            print(f"Error sending welcome email: {e}")

        return redirect(url_for('login'))
    return render_template('register.html')

# ---------------- LOGIN ----------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        response = users_table.get_item(Key={'email': email})
        user = response.get('Item')

        if user and check_password_hash(user['hashed_password'], password):
            session['user_id'] = user['user_id']
            session['email'] = user['email']
            session['username'] = user['username']

            users_table.update_item(
                Key={'email': email},
                UpdateExpression="SET login_count = if_not_exists(login_count, :start) + :inc",
                ExpressionAttributeValues={':inc': 1, ':start': 0}
            )

            flash('Login successful!', 'success')
            return redirect(url_for('user_dashboard'))
        else:
            return render_template('login.html', error="Invalid credentials")
    return render_template('login.html')

# ---------------- DASHBOARD ----------------
@app.route('/user_dashboard')
@login_required
def user_dashboard():
    email = session['email']
    bookings = query_by_email_scan(bookings_table,email)
    bus_bookings = query_by_email_scan(bus_bookings_table,email)
    train_bookings = query_by_email_scan(train_bookings_table,email)
    hotel_bookings = query_by_email_scan(hotel_bookings_table,email)
    trip_plans = sorted(query_by_email_scan(trip_plans_table,email), key=lambda x: x.get('created_at', ''), reverse=True)

    return render_template('user_dashboard.html',
                           bookings=bookings,
                           bus_bookings=bus_bookings,
                           train_bookings=train_bookings,
                           hotel_bookings=hotel_bookings,
                           trip_plans=trip_plans)

# ---------------- SEARCH ----------------
@app.route('/search_places', methods=['GET'])
def search_places():
    query = request.args.get('location', '').strip().lower()
    category = request.args.get('category', '').strip().lower()

    if not query:
        return render_template("search_results.html", results=[], message="Please enter a location to search.")

    response = places_table.scan(
        FilterExpression=Attr('location').contains(query) | Attr('name').contains(query)
    )
    results = response.get('Items', [])

    if category:
        results = [r for r in results if category in r.get('category', '').lower()]

    return render_template("search_results.html", results=results[:20], message=None)

def get_place_by_id(place_id):
    try:
        response = places_table.get_item(Key={'place_id': place_id})
        return response.get('Item')
    except:
        return None

@app.route('/place/<string:place_id>')
def place_detail(place_id):
    place = get_place_by_id(place_id)
    if not place:
        abort(404)
    return render_template('place_detail.html', place=place)

# ---------------- PLAN TRIP ----------------
@app.route('/plan_trip/<place_id>', methods=['GET', 'POST'])
@login_required
def plan_trip(place_id):
    place = get_place_by_id(place_id)
    if not place:
        return "Place not found", 404

    if request.method == 'POST':
        start_date = request.form.get('start_date')
        end_date = request.form.get('end_date')
        notes = request.form.get('notes')

        trip_id = str(uuid.uuid4())
        trip_plans_table.put_item(Item={
            'trip_id': trip_id,
            'email': session['email'],
            'place_id': place_id,
            'place_name': place['name'],
            'location': place.get('location', ''),
            'image': place.get('image', ''),
            'start_date': start_date,
            'end_date': end_date,
            'notes': notes,
            'status': 'planned',
            'created_at': datetime.utcnow().isoformat()
        })

        try:
            sns_client.publish(
                TopicArn=SNS_TOPIC_ARN,
                Message=f"You have planned a trip to {place['name']} from {start_date} to {end_date}.",
                Subject="Trip Planned on TravelGo"
            )
        except ClientError as e:
            print(f"Error sending trip email: {e}")

        return redirect(url_for('user_dashboard'))

    return render_template('plan_trip.html', place=place)

# ---------------- TEST SNS EMAIL ENDPOINT ----------------
@app.route('/send_test_email')
def send_test_email():
    try:
        sns_client.publish(
            TopicArn=SNS_TOPIC_ARN,
            Message="This is a test email from TravelGo.",
            Subject="Test Email from TravelGo"
        )
        return "Test email sent via SNS."
    except ClientError as e:
        return f"Error: {e}"


@app.route('/remove_trip_plan', methods=['POST'])
@login_required
def remove_trip_plan():
    trip_id = request.form.get('trip_id')
    if trip_id:
        trip_plans_table.delete_item(Key={'trip_id': trip_id})
        flash('Trip plan removed successfully!', 'success')
    else:
        flash('Invalid trip ID.', 'danger')
    return redirect(url_for('user_dashboard'))

@app.route('/finish_trip_plan', methods=['POST'])
@login_required
def finish_trip_plan():
    trip_id = request.form.get('trip_id')
    if trip_id:
        trip_plans_table.update_item(
            Key={'trip_id': trip_id},
            UpdateExpression="SET #s = :val",
            ExpressionAttributeNames={'#s': 'status'},
            ExpressionAttributeValues={':val': 'finished'}
        )
        flash('Trip marked as finished!', 'info')
    else:
        flash('Invalid trip ID.', 'danger')
    return redirect(url_for('user_dashboard'))

# ---------------- VIRTUAL EXHIBITION ----------------
@app.route('/virtual_exhibition', methods=['GET', 'POST'])
@login_required
def virtual_exhibition():
    if request.method == 'POST':
        data = request.get_json()
        required_fields = ['name', 'details', 'image']
        if not all(field in data for field in required_fields):
            return jsonify({'success': False, 'message': 'Missing data'})

        wishlist_table.put_item(Item={
            'wishlist_id': str(uuid.uuid4()),
            'email': session['email'],
            'item_id': data['name'],
            'item_name': data['name'],
            'item_details': data['details'],
            'item_image': data['image'],
            'added_date': datetime.utcnow().isoformat()
        })

        return jsonify({'success': True, 'message': f'Added "{data["name"]}" to wishlist successfully!'})
    return render_template('virtual_exhibition.html', active='Exhibition')

# ---------------- WISHLIST ----------------
@app.route('/add_to_wishlist', methods=['POST'])
@login_required
def add_to_wishlist():
    data = request.get_json()
    email = session['email']

    # Check if item already exists
    response = wishlist_table.scan(
        FilterExpression=Attr('email').eq(email) & Attr('item_id').eq(data['item_id'])
    )
    if response.get('Items'):
        return jsonify({'success': False, 'message': 'Item already in wishlist'})

    wishlist_table.put_item(Item={
        'wishlist_id': str(uuid.uuid4()),
        'email': email,
        'item_id': data['item_id'],
        'item_name': data['item_name'],
        'item_details': data['item_details'],
        'added_date': datetime.utcnow().isoformat()
    })
    return jsonify({'success': True, 'message': 'Item added to wishlist'})

@app.route('/wishlist')
@login_required
def wishlist():
    return render_template('wishlist.html')

@app.route('/wishlist_data')
@login_required
def wishlist_data():
    response = wishlist_table.scan(
        FilterExpression=Attr('email').eq(session['email'])
    )
    items = response.get('Items', [])
    for item in items:
        item['image'] = item.get('item_image', '')
        item['details'] = item.get('item_details', '')
    return jsonify({'wishlist': items})

@app.route('/remove_from_wishlist', methods=['POST'])
@login_required
def remove_from_wishlist():
    item_id = request.json.get('item_id')
    if not item_id:
        return jsonify({'success': False, 'message': 'Item ID not provided'})

    # Scan to find the wishlist_id
    response = wishlist_table.scan(
        FilterExpression=Attr('email').eq(session['email']) & Attr('item_id').eq(item_id)
    )
    items = response.get('Items', [])
    if items:
        wishlist_table.delete_item(Key={'wishlist_id': items[0]['wishlist_id']})
        return jsonify({'success': True, 'message': 'Item removed from wishlist'})
    return jsonify({'success': False, 'message': 'Item not found'})

# ---------------- QUIZ ----------------
quiz_questions = [
    {"question": "Which country is home to the Eiffel Tower?", "answer": "France"},
    {"question": "Where is the Great Barrier Reef located?", "answer": "Australia"},
    {"question": "Which city is known as the Big Apple?", "answer": "New York"}
]

@app.route('/quiz', methods=['GET', 'POST'])
def quiz():
    if 'quiz_index' not in session:
        session['quiz_index'] = 0
        session['quiz_correct'] = 0

    index = session['quiz_index']

    if request.method == 'POST':
        user_answer = request.form['answer'].strip().lower()
        correct_answer = quiz_questions[index]['answer'].strip().lower()

        if user_answer == correct_answer:
            session['quiz_correct'] += 1

        session['quiz_index'] += 1
        index = session['quiz_index']

        if index >= len(quiz_questions):
            won = session['quiz_correct'] == len(quiz_questions)
            session.pop('quiz_index', None)
            session.pop('quiz_correct', None)
            return render_template('quiz.html', result_shown=True, won=won)

    if index < len(quiz_questions):
        question = quiz_questions[index]['question']
        return render_template('quiz.html', result_shown=False, question=question)

    return redirect(url_for('quiz'))
#-------------booking--------------------
# Dummy flight data — can be moved to a separate module later
flights_data = [
    {
        'id': 1,
        'flight_number': 'AI101',
        'origin': 'Delhi',
        'destination': 'Mumbai',
        'departure_time': '2025-07-01 08:00',
        'arrival_time': '2025-07-01 10:00',
        'price': 5000
    },
    {
        'id': 2,
        'flight_number': 'AI102',
        'origin': 'Delhi',
        'destination': 'Mumbai',
        'departure_time': '2025-07-01 12:00',
        'arrival_time': '2025-07-01 14:00',
        'price': 5500
    },
    {
        'id': 3,
        'flight_number': '6E201',
        'origin': 'Delhi',
        'destination': 'Bangalore',
        'departure_time': '2025-07-01 09:00',
        'arrival_time': '2025-07-01 11:45',
        'price': 6200
    },
    {
        'id': 4,
        'flight_number': 'SG303',
        'origin': 'Mumbai',
        'destination': 'Chennai',
        'departure_time': '2025-07-02 14:30',
        'arrival_time': '2025-07-02 16:50',
        'price': 4900
    }
]
for flight in flights_data:
    flight['seats'] = {
        f"{row}{col}": "available"
        for row in ['A', 'B', 'C', 'D', 'E', 'F']
        for col in range(1, 5)
    }

buses_data = [
    {
        'id': 1,
        'origin': 'Pune',
        'destination': 'Mumbai',
        'departure': '2025-06-25',
        'departure_time': '08:00 AM',
        'arrival_time': '01:00 PM',
        'operator': 'RedBus',
        'bus_number': 'RB101',
        'price': 500
    },
    {
        'id': 2,
        'origin': 'Pune',
        'destination': 'Mumbai',
        'departure': '2025-06-25',
        'departure_time': '10:30 AM',
        'arrival_time': '03:30 PM',
        'operator': 'Neeta Travels',
        'bus_number': 'NT202',
        'price': 550
    },
    {
        'id': 3,
        'origin': 'Delhi',
        'destination': 'Agra',
        'departure': '2025-06-26',
        'departure_time': '07:00 AM',
        'arrival_time': '10:00 AM',
        'operator': 'Volvo Express',
        'bus_number': 'VX303',
        'price': 700
    }
]
bus_seat_layout = {
    f"{row}{col}": "available"
    for row in range(1, 7)
    for col in ['A', 'B', 'C', 'D']
}

for bus in buses_data:
    if 'seats' not in bus:
        bus['seats'] = bus_seat_layout.copy()


trains_data = [
    {
        'id': 1,
        'train_number': '12345',
        'origin': 'Delhi',
        'destination': 'Mumbai',
        'departure_time': '08:00 AM',
        'arrival_time': '08:00 PM',
        'date': '2025-06-25',
        'price': 850
    },
    {
        'id': 2,
        'train_number': '54321',
        'origin': 'Mumbai',
        'destination': 'Delhi',
        'departure_time': '06:00 AM',
        'arrival_time': '06:30 PM',
        'date': '2025-06-26',
        'price': 900
    },
    {
        'id': 3,
        'train_number': '67890',
        'origin': 'Bangalore',
        'destination': 'Chennai',
        'departure_time': '09:30 AM',
        'arrival_time': '02:45 PM',
        'date': '2025-06-25',
        'price': 550
    },
    {
        'id': 4,
        'train_number': '98765',
        'origin': 'Kolkata',
        'destination': 'Patna',
        'departure_time': '11:15 AM',
        'arrival_time': '06:00 PM',
        'date': '2025-06-25',
        'price': 620
    }
]
train_seats = {
    f"{row}{col}": "available"
    for row in range(1, 6)  # Rows 1-5
    for col in ['A', 'B', 'C', 'D']  # 4 seats per row
}

for train in trains_data:
    train['seats'] = train_seats.copy()


hotels_data = [
    {
        'id': 1,
        'name': 'The Grand Palace',
        'location': 'New York',
        'price_per_night': 180,
        'rating': 4.5,
        'rooms': 25,
        'image_url': 'https://images.unsplash.com/photo-1586098311386-efc7e5267982?q=80&w=1170&auto=format&fit=crop&ixlib=rb-4.1.0&ixid=M3wxMjA3fDB8MHxwaG90by1wYWdlfHx8fGVufDB8fHx8fA%3D%3D'
    },
    {
        'id': 2,
        'name': 'Beachside Resort',
        'location': 'Los Angeles',
        'price_per_night': 220,
        'rating': 4.7,
        'rooms': 15,
        'image_url': 'https://via.placeholder.com/300x200?text=Beachside+Resort'
    },
    {
        'id': 3,
        'name': 'Urban Stay',
        'location': 'Chicago',
        'price_per_night': 140,
        'rating': 4.2,
        'rooms': 30,
        'image_url': 'https://via.placeholder.com/300x200?text=Urban+Stay'
    },
    {
        'id': 4,
        'name': 'Mountain View Inn',
        'location': 'Denver',
        'price_per_night': 160,
        'rating': 4.4,
        'rooms': 20,
        'image_url': 'https://via.placeholder.com/300x200?text=Mountain+View+Inn'
    },
    {
        'id': 5,
        'name': 'City Center Suites',
        'location': 'New York',
        'price_per_night': 200,
        'rating': 4.6,
        'rooms': 18,
        'image_url': 'https://plus.unsplash.com/premium_photo-1676165629938-74bdba5ae3b5?q=80&w=687&auto=format&fit=crop&ixlib=rb-4.1.0&ixid=M3wxMjA3fDB8MHxwaG90by1wYWdlfHx8fGVufDB8fHx8fA%3D%3D'
    }
]

@app.route('/confirm_booking/<int:flight_id>', methods=['GET', 'POST'])
@login_required
def confirm_booking(flight_id):
    response = flights_table.scan(FilterExpression=Attr('id').eq(flight_id))
    items = response.get('Items', [])
    flight = items[0] if items else None

    passenger_names = request.form.getlist('passenger_names[]')
    passenger_count = int(session.get('passengers', 1))

    if not flight:
        flash("Flight not found.", "danger")
        return redirect(url_for('booking'))

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'cancel':
            flash("Booking cancelled.", "info")
            return redirect(url_for('booking'))

        if action == 'confirm':
            selected_seats = request.form.getlist('seats')

            if len(selected_seats) != passenger_count:
                flash(f"You must select exactly {passenger_count} seat(s).", "warning")
                return redirect(url_for('confirm_booking', flight_id=flight_id))

            for seat, pname in zip(selected_seats, passenger_names):
                booking_data = {
                    "booking_id": str(uuid.uuid4()),
                    "email": session['email'],
                    "flight_id": flight_id,
                    "flight_number": flight.get('flight_number'),
                    "origin": flight.get('origin'),
                    "destination": flight.get('destination'),
                    "departure_time": flight.get('departure_time'),
                    "arrival_time": flight.get('arrival_time'),
                    "price": flight.get('price'),
                    "passenger_name": pname,
                    "seat": seat,
                    "booked_at": datetime.utcnow().isoformat()
                }
                bookings_table.put_item(Item=booking_data)
                flight['seats'][seat] = 'booked'

            flights_table.update_item(
                Key={'flight_id': flight['flight_id']},
                UpdateExpression="SET seats = :s",
                ExpressionAttributeValues={':s': flight['seats']}
            )

            try:
                sns_client.publish(
                    TopicArn=SNS_TOPIC_ARN,
                    Message=f"Your flight booking from {flight['origin']} to {flight['destination']} is confirmed. Flight No: {flight['flight_number']}.",
                    Subject="Flight Booking Confirmed"
                )
            except ClientError as e:
                print(f"SNS Error: {e}")

            flash("Booking confirmed!", "success")
            return redirect(url_for('user_dashboard'))

    return render_template('confirm_booking.html', flight=flight, passengers=passenger_count)


@app.route('/bus/confirm/<int:bus_id>', methods=['GET', 'POST'])
@login_required
def confirm_bus_booking(bus_id):
    bus = next((b for b in buses_data if b['id'] == bus_id), None)
    passenger_names = request.form.getlist('passenger_names[]')

    if not bus:
        return "Bus not found", 404

    if request.method == 'POST':
        action = request.form.get('action')
        selected_seats = request.form.getlist('seats')

        if action == 'confirm':
            if not selected_seats:
                flash('Please select at least one seat.', 'danger')
                return redirect(request.url)

            for seat in selected_seats:
                if bus['seats'].get(seat) == 'booked':
                    flash(f"Seat {seat} already booked.", "danger")
                    return redirect(request.url)

            for i, seat in enumerate(selected_seats):
                bus['seats'][seat] = 'booked'
                bus_bookings_table.put_item(Item={
                    'booking_id': str(uuid.uuid4()),
                    'email': session['email'],
                    'bus_id': bus['id'],
                    'bus_number': bus['bus_number'],
                    'origin': bus['origin'],
                    'destination': bus['destination'],
                    'departure_time': bus['departure_time'],
                    'arrival_time': bus['arrival_time'],
                    'price': bus['price'],
                    'seat': seat,
                    'passenger_name': passenger_names[i] if i < len(passenger_names) else '',
                    'booked_at': datetime.utcnow().isoformat()
                })

            try:
                sns_client.publish(
                    TopicArn=SNS_TOPIC_ARN,
                    Message=f"Your bus booking from {bus['origin']} to {bus['destination']} is confirmed. Bus No: {bus['bus_number']}.",
                    Subject="Bus Booking Confirmed"
                )
            except ClientError as e:
                print(f"SNS Error: {e}")

            flash(f'Bus booking confirmed! Seats: {", ".join(selected_seats)}', 'success')
            return redirect(url_for('user_dashboard'))
        else:
            flash('Bus booking cancelled.', 'info')
            return redirect(url_for('booking'))

    return render_template('confirm_bus_booking.html', bus=bus, passengers=session.get('passengers', 1))


@app.route('/train/confirm/<int:train_id>', methods=['GET', 'POST'])
@login_required
def confirm_train_booking(train_id):
    train = next((t for t in trains_data if t['id'] == train_id), None)
    if not train:
        return "Train not found", 404

    try:
        passenger_count = int(request.args.get('passengers') or session.get('passengers', 1))
    except ValueError:
        passenger_count = 1

    if request.method == 'POST':
        action = request.form.get('action')
        selected_seats = request.form.getlist('seats[]')

        if action == 'cancel':
            flash("Train booking cancelled.", "info")
            return redirect(url_for('booking'))

        if not selected_seats:
            flash("Please select at least one seat before confirming.", "warning")
            return redirect(request.url)

        if len(selected_seats) > passenger_count:
            flash(f"You selected {len(selected_seats)} seats but only {passenger_count} passenger(s).", "danger")
            return redirect(request.url)

        passenger_names = request.form.getlist('passenger_names[]')
        if len(passenger_names) != len(selected_seats):
            flash("Number of passenger names does not match selected seats.", "danger")
            return redirect(request.url)

        for seat, name in zip(selected_seats, passenger_names):
            train['seats'][seat] = 'booked'
            train_bookings_table.put_item(Item={
                'booking_id': str(uuid.uuid4()),
                'email': session['email'],
                'train_id': train['id'],
                'train_number': train['train_number'],
                'origin': train['origin'],
                'destination': train['destination'],
                'departure_time': train['departure_time'],
                'arrival_time': train['arrival_time'],
                'date': train['date'],
                'price': train['price'],
                'seat': seat,
                'passenger_name': name,
                'booked_at': datetime.utcnow().isoformat()
            })

        try:
            sns_client.publish(
                TopicArn=SNS_TOPIC_ARN,
                Message=f"Your train booking from {train['origin']} to {train['destination']} is confirmed. Train No: {train['train_number']}.",
                Subject="Train Booking Confirmed"
            )
        except ClientError as e:
            print(f"SNS Error: {e}")

        flash(f"Train booking confirmed for seat(s): {', '.join(selected_seats)}", "success")
        return redirect(url_for('user_dashboard'))

    return render_template('confirm_train_booking.html', train=train, num_passengers=passenger_count)


@app.route('/hotel/confirm/<int:hotel_id>', methods=['GET', 'POST'])
@login_required
def confirm_hotel_booking(hotel_id):
    hotel = next((h for h in hotels_data if h['id'] == hotel_id), None)
    if not hotel:
        return "Hotel not found", 404

    check_in = session.get('check_in')
    check_out = session.get('check_out')

    try:
        nights = (datetime.strptime(check_out, '%Y-%m-%d') - datetime.strptime(check_in, '%Y-%m-%d')).days
        if nights <= 0:
            nights = 1
    except (TypeError, ValueError):
        nights = 1

    if request.method == 'POST':
        if request.form['action'] == 'confirm':
            hotel_bookings_table.put_item(Item={
                'booking_id': str(uuid.uuid4()),
                'email': session['email'],
                'hotel_id': hotel['id'],
                'hotel_name': hotel['name'],
                'location': hotel['location'],
                'price': hotel['price_per_night'],
                'rating': hotel['rating'],
                'check_in': check_in,
                'check_out': check_out,
                'booked_at': datetime.utcnow().isoformat()
            })

            try:
                sns_client.publish(
                    TopicArn=SNS_TOPIC_ARN,
                    Message=f"Your hotel booking at {hotel['name']} in {hotel['location']} is confirmed from {check_in} to {check_out}.",
                    Subject="Hotel Booking Confirmed"
                )
            except ClientError as e:
                print(f"SNS Error: {e}")

            flash('Hotel booking confirmed!', 'success')
            return redirect(url_for('user_dashboard'))
        else:
            flash('Hotel booking cancelled.', 'info')
            return redirect(url_for('booking'))
    return render_template(
        'confirm_hotel_booking.html',
        hotel=hotel,
        check_in=check_in,
        check_out=check_out,
        nights=nights
    )   


@app.route('/cancel_booking', methods=['POST'])
@login_required
def cancel_booking():
    user_email = session.get('email')
    flight_id = request.form.get('flight_id')

    if user_email and flight_id:
        # Scan to find the booking
        response = bookings_table.scan(
            FilterExpression=Attr('email').eq(user_email) & Attr('flight_id').eq(int(flight_id))
        )
        items = response.get('Items', [])
        if items:
            bookings_table.delete_item(Key={'booking_id': items[0]['booking_id']})

            if session.get('last_booking') and session['last_booking']['id'] == int(flight_id):
                session.pop('last_booking')

            flash("Your booking has been canceled.", "warning")
        else:
            flash("No booking found to cancel.", "danger")
    else:
        flash("Unable to cancel booking.", "danger")

    return redirect(url_for('user_dashboard'))

@app.route('/cancel_bus_booking', methods=['POST'])
@login_required
def cancel_bus_booking():
    bus_id = int(request.form['bus_id'])
    response = bus_bookings_table.scan(
        FilterExpression=Attr('email').eq(session['email']) & Attr('bus_id').eq(bus_id)
    )
    items = response.get('Items', [])
    if items:
        bus_bookings_table.delete_item(Key={'booking_id': items[0]['booking_id']})
        flash('Bus booking cancelled.', 'info')
    else:
        flash('No booking found to cancel.', 'warning')
    return redirect(url_for('user_dashboard'))

@app.route('/cancel_train_booking', methods=['POST'])
@login_required
def cancel_train_booking():
    train_id = int(request.form['train_id'])
    response = train_bookings_table.scan(
        FilterExpression=Attr('email').eq(session['email']) & Attr('train_id').eq(train_id)
    )
    items = response.get('Items', [])
    if items:
        train_bookings_table.delete_item(Key={'booking_id': items[0]['booking_id']})
        flash('Train booking cancelled.', 'info')
    else:
        flash('No booking found to cancel.', 'warning')
    return redirect(url_for('user_dashboard'))

@app.route('/cancel_hotel_booking', methods=['POST'])
@login_required
def cancel_hotel_booking():
    hotel_id = int(request.form['hotel_id'])
    response = hotel_bookings_table.scan(
        FilterExpression=Attr('email').eq(session['email']) & Attr('hotel_id').eq(hotel_id)
    )
    items = response.get('Items', [])
    if items:
        hotel_bookings_table.delete_item(Key={'booking_id': items[0]['booking_id']})
        flash('Hotel booking cancelled.', 'info')
    else:
        flash('No booking found to cancel.', 'warning')
    return redirect(url_for('user_dashboard'))

# ---------------- LOGOUT ----------------
@app.route('/logout')
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for('login'))

# ---------------- RUN ----------------
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80, debug=True)