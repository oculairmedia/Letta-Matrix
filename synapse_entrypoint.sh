#!/bin/sh
set -e

CONFIG_FILE="/data/homeserver.yaml"
LOG_CONFIG_FILE="/data/${SYNAPSE_SERVER_NAME}.log.config" # Use SYNAPSE_SERVER_NAME for log config
SIGNING_KEY_FILE="/data/${SYNAPSE_SERVER_NAME}.signing.key"

# Check if the main config file, log config, or signing key is missing.
# The generate command creates all of these. If one is missing, assume we need to generate.
if [ ! -f "$CONFIG_FILE" ] || [ ! -f "$LOG_CONFIG_FILE" ] || [ ! -f "$SIGNING_KEY_FILE" ]; then
  echo "Configuration files not found. Generating new configuration..."
  echo "Using SYNAPSE_SERVER_NAME: ${SYNAPSE_SERVER_NAME}"
  echo "Using SYNAPSE_REPORT_STATS: ${SYNAPSE_REPORT_STATS}"
  
  # Ensure data directory exists and has correct permissions for synapse user (typically UID 991)
  # The official image's start.py usually handles chown, but explicit can't hurt.
  mkdir -p /data
  chown -R 991:991 /data || echo "Chown failed, continuing..."

  gosu 991:991 /usr/local/bin/python -m synapse.app.homeserver \
    --server-name "${SYNAPSE_SERVER_NAME}" \
    --report-stats "${SYNAPSE_REPORT_STATS}" \
    --config-path "${CONFIG_FILE}" \
    --config-directory /data \
    --data-directory /data \
    --generate-config \
    --open-private-ports \
    --enable-registration # Added to enable registration by default
  
  echo "Configuration generated."
  
  # Ensure enable_registration_without_verification is true
  if ! grep -q "enable_registration_without_verification: true" "$CONFIG_FILE"; then
    echo "Adding enable_registration_without_verification: true to $CONFIG_FILE"
    # Check if the setting exists with false and replace it, otherwise append.
    if grep -q "enable_registration_without_verification:" "$CONFIG_FILE"; then
      # Use a temporary file for sed in-place edit to be safe
      sed -i.bak "s/enable_registration_without_verification:.*$/enable_registration_without_verification: true/" "$CONFIG_FILE" && rm -f "$CONFIG_FILE.bak"
    else
      echo "\nenable_registration_without_verification: true" >> "$CONFIG_FILE"
    fi
  else
    echo "enable_registration_without_verification is already true in $CONFIG_FILE"
  fi

else
  echo "Existing configuration found at $CONFIG_FILE. Skipping generation."
  # Even if config exists, ensure enable_registration_without_verification is true
  # This handles cases where an old config might be present without this crucial line
  if ! grep -q "enable_registration_without_verification: true" "$CONFIG_FILE"; then
    echo "Adding/Updating enable_registration_without_verification: true to existing $CONFIG_FILE"
    if grep -q "enable_registration_without_verification:" "$CONFIG_FILE"; then
      sed -i.bak "s/enable_registration_without_verification:.*$/enable_registration_without_verification: true/" "$CONFIG_FILE" && rm -f "$CONFIG_FILE.bak"
    else
      echo "\nenable_registration_without_verification: true" >> "$CONFIG_FILE"
    fi
  fi
fi

# Now, execute the original entrypoint/CMD of the Synapse image to start the server
# The original entrypoint is /start.py
echo "Starting Synapse server..."
exec /start.py