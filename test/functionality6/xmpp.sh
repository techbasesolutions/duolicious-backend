#!/usr/bin/env bash

# Match-before-message gate, end to end over real websockets (2026-07-29).
#
# Rule (shipped 2026-07-22 after the silent-block incident): only MATCHED
# members can exchange chat messages. The transport must:
#   1. answer an unmatched sender with <duo_message_blocked/> and persist
#      NOTHING, and
#   2. deliver a matched pair's message (receipt, MAM rows, inbox row).
#
# Prereqs: postgres + api + chat + chattest containers from
# docker-compose.test.yml. Override CHATTEST_PORT if host port 3000 is
# taken (e.g. CHATTEST_PORT=3005 with a remapped chattest).

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$script_dir"

source ../util/setup.sh

set -xe

CHATTEST="http://localhost:${CHATTEST_PORT:-3000}"

sleep 3 # MongooseIM takes some time to flush messages to the DB

q "delete from person"
q "delete from duo_session"
q "delete from mam_message"
q "delete from inbox"
q "delete from intro_hash"
q "delete from ahavah_match"

../util/create-user.sh user1 0 0
../util/create-user.sh user2 0 0

assume_role user1 ; user1token=$SESSION_TOKEN
assume_role user2 ; user2token=$SESSION_TOKEN

user1uuid=$(get_uuid 'user1@example.com')
user2uuid=$(get_uuid 'user2@example.com')

user1id=$(get_id 'user1@example.com')
user2id=$(get_id 'user2@example.com')

connect_as_user1 () {
  curl -X POST "$CHATTEST/config" -H "Content-Type: application/json" -d '{
    "service": "ws://chat:5443",
    "domain": "ahavah.app",
    "resource": "testresource",
    "username": "'$user1uuid'",
    "password": "'$user1token'"
  }'
  sleep 1
  curl -sX GET "$CHATTEST/pop" > /dev/null
}

send_as_user1 () {
  local id=$1
  local body=$2
  curl -X POST "$CHATTEST/send" -H "Content-Type: application/xml" -d "
  <message
      type='chat'
      from='$user1uuid@ahavah.app'
      to='$user2uuid@ahavah.app'
      id='$id'
      check_uniqueness='false'
      xmlns='jabber:client'>
    <body>$body</body>
    <request xmlns='urn:xmpp:receipts'/>
  </message>
  "
}

say "Unmatched pair: the message is blocked and nothing persists"

connect_as_user1

send_as_user1 gate1 'hello before match'

sleep 3

curl -sX GET "$CHATTEST/pop" | grep -qF '<duo_message_blocked id="gate1"'

[[ "$(q "select count(*) from mam_message where \
    search_body = 'hello before match'")" = 0 ]]

[[ "$(q "select count(*) from inbox")" = 0 ]]


say "Matched pair: the message is delivered, archived, and inboxed"

q "insert into ahavah_match (user_a_id, user_b_id) values \
    (least($user1id, $user2id), greatest($user1id, $user2id))"

sleep 6 # fetch_is_matched AsyncLruCache ttl is 5s

send_as_user1 gate2 'hello after match'

sleep 4 # MongooseIM takes some time to flush messages to the DB

curl -sX GET "$CHATTEST/pop" | grep -qF '<duo_message_delivered id="gate2"/>'

# One logical message = two MAM rows (sender copy + recipient copy).
[[ "$(q "select count(*) from mam_message where \
    search_body = 'hello after match'")" = 2 ]]

# The recipient's inbox carries the unread conversation.
[[ "$(q "select count(*) from inbox where \
    luser = '${user2uuid}' and \
    remote_bare_jid = '${user1uuid}@ahavah.app' and \
    unread_count > 0")" = 1 ]]

say "PASS: match gate blocks strangers and delivers for matches"
