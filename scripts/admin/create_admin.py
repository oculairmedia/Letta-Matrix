#!/usr/bin/env python3

import requests
import hashlib
import hmac
import json

# Configuration
homeserver_url = "http://localhost:8008"
registration_shared_secret = "-XBDVT.:eZhMLVM_#Jt*@WQhehHQvIWf^4+7XSA2v;J6Mp:GK@"
username = "matrixadmin"
password = "admin123"
admin = True

# Generate nonce
nonce_response = requests.get(f"{homeserver_url}/_synapse/admin/v1/register")
nonce = nonce_response.json()["nonce"]

# Create MAC
mac_content = f"{nonce}\0{username}\0{password}\0{'admin' if admin else 'notadmin'}"
mac = hmac.new(
    registration_shared_secret.encode('utf8'),
    mac_content.encode('utf8'),
    hashlib.sha1
).hexdigest()

# Register user
register_data = {
    "nonce": nonce,
    "username": username,
    "password": password,
    "admin": admin,
    "mac": mac
}

response = requests.post(
    f"{homeserver_url}/_synapse/admin/v1/register",
    json=register_data
)

print("Response:", response.status_code)
print(response.json())