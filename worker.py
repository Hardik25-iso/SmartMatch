import pika
import json
import os
import time
from datetime import datetime
from dotenv import load_dotenv
from flask import Flask
from flask_sqlalchemy import SQLAlchemy

# --- SETUP FLASK APP CONTEXT ---
# This worker needs to talk to the database, so it needs a Flask app
# context and the database models.

load_dotenv()

app = Flask(__name__)

# Configure the app with the same database URI
DB_USER = os.environ.get('DB_USER')
DB_PASSWORD = os.environ.get('DB_PASSWORD')
DB_HOST = os.environ.get('DB_HOST')
DB_NAME = os.environ.get('DB_NAME')
app.config['SQLALCHEMY_DATABASE_URI'] = f'mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}/{DB_NAME}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# --- IMPORT MODELS ---
# We must define the models here just as in app.py so the worker
# understands the database structure.
class ItemNeeded(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item_name = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    quantity_needed = db.Column(db.Integer, nullable=False)
    is_urgent = db.Column(db.Boolean, default=False, nullable=False)
    date_posted = db.Column(db.DateTime, server_default=db.func.now())
    status = db.Column(db.String(20), default='Active')
    orphanage_id = db.Column(db.Integer, db.ForeignKey('orphanage.id'), nullable=False)
    pledged_by = db.Column(db.String(100), nullable=True)
    pledge_timestamp = db.Column(db.DateTime, nullable=True)

# --- WORKER CALLBACK ---
def process_pledge_callback(ch, method, properties, body):
    print(f" [x] Received pledge message: {body.decode()}")
    
    try:
        data = json.loads(body)
        item_id = data.get('item_id')
        donor_name = data.get('donor_name')

        # Use 'with app.app_context():' to safely interact with the database
        with app.app_context():
            # Find the item. We use 'with_for_update=True'
            # This LOCKS the database row until we are done (db.session.commit())
            # This is the *correct* way to prevent a race condition.
            item = ItemNeeded.query.with_for_update().get(item_id)

            if not item:
                print(f" [!] Item ID {item_id} not found. Skipping.")
            elif item.status != 'Active':
                print(f" [!] Item ID {item_id} is no longer 'Active'. Status is '{item.status}'. Skipping.")
            else:
                # Success! Update the item.
                item.status = 'Pledged'
                item.pledged_by = donor_name
                item.pledge_timestamp = datetime.utcnow()
                db.session.commit()
                print(f" [x] Successfully pledged item {item_id} to {donor_name}.")

        # Acknowledge the message (tells RabbitMQ we processed it)
        ch.basic_ack(delivery_tag=method.delivery_tag)
        
    except Exception as e:
        print(f" [!] Error processing message: {e}")
        # Don't acknowledge the message, so it can be re-queued and tried again.
        ch.basic_nack(delivery_tag=method.delivery_tag)

# --- MAIN WORKER LOOP ---
def main():
    MQ_URL = os.environ.get('MQ_URL')
    while True:
        try:
            connection = pika.BlockingConnection(pika.URLParameters(MQ_URL))
            channel = connection.channel()
            channel.queue_declare(queue='pledge_queue', durable=True)
            
            # Only fetch 1 message at a time.
            channel.basic_qos(prefetch_count=1) 
            
            channel.basic_consume(
                queue='pledge_queue',
                on_message_callback=process_pledge_callback
            )

            print(' [*] Worker is waiting for pledge messages. To exit press CTRL+C')
            channel.start_consuming()

        except pika.exceptions.AMQPConnectionError as e:
            print(f"Connection to RabbitMQ failed: {e}. Retrying in 5 seconds...")
            time.sleep(5)
        except KeyboardInterrupt:
            print(" [i] Worker shutting down.")
            break

if __name__ == '__main__':
    main()