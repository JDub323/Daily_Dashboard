#!/usr/bin/env bash
# Build the spike for the FR165 and sign it with the developer key.
#
# Nothing here is on PATH on purpose: the JDK is an unzipped Temurin rather than
# an installed one (the MSI wanted an elevation prompt that never appeared), and
# the SDK is wherever the SDK Manager keeps it. Both are named once, here.
set -euo pipefail

CIQ="$APPDATA/Garmin/ConnectIQ"
SDK="$CIQ/Sdks/connectiq-sdk-win-9.2.0-2026-06-09-92a1605b2"
export JAVA_HOME="$CIQ/jdk/jdk-17.0.20.1+1"
export PATH="$JAVA_HOME/bin:$PATH"

cd "$(dirname "$0")"

# inout.prg, not spike.prg: the log file the watch writes is named after the
# PRG, so this is what puts the results in \GARMIN\APPS\LOGS\INOUT.TXT.
"$SDK/bin/monkeyc.bat" \
    -o inout.prg \
    -f monkey.jungle \
    -y "$CIQ/developer_key.der" \
    -d "${1:-fr165}" \
    -w

echo "built: $(pwd)/inout.prg"
