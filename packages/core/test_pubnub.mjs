/* eslint-disable */
import PubNub from 'pubnub';
import fs from 'fs';
import path from 'path';

const CLUBCARD_PUBNUB_SUB_KEY = 'sub-c-a4abea84-9ca3-11ea-8e71-f2b83ac9263d';
const CLUBCARD_PUBNUB_PUB_KEY = 'pub-c-6878d382-5ae6-4494-9099-f930f938868b';

async function main() {
  const authPath = '/home/horrible/horrible-dashboard/.data/clubhouse-auth.json';
  if (!fs.existsSync(authPath)) {
    console.error('No auth file found.');
    return;
  }
  const auth = JSON.parse(fs.readFileSync(authPath, 'utf8'));
  const myUserId = auth.user_id;

  // Let's get the list of active channels from our local server
  console.log('Fetching live channels...');
  const channelsRes = await fetch('http://127.0.0.1:8000/api/clubhouse/channels', {
    headers: { 'Authorization': `Token ${auth.auth_token}`, 'CH-UserID': String(myUserId) }
  }).then(r => r.json());

  const channels = channelsRes.channels || [];
  if (channels.length === 0) {
    console.log('No live rooms found right now.');
    return;
  }

  const channelName = channels[0].channel;
  console.log(`Joining channel: ${channelName}...`);

  const joinRes = await fetch(`http://127.0.0.1:8000/api/clubhouse/channels/${channelName}/join`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' }
  }).then(r => r.json());

  console.log('Join response:', {
    success: joinRes.success,
    pubnub_enable: joinRes.pubnub_enable,
    pubnub_token_len: joinRes.pubnub_token?.length,
    pubnub_origin: joinRes.pubnub_origin
  });

  if (!joinRes.pubnub_enable || !joinRes.pubnub_token) {
    console.error('PubNub not enabled or token missing.');
    return;
  }

  const pubnub = new PubNub({
    subscribeKey: CLUBCARD_PUBNUB_SUB_KEY,
    publishKey: CLUBCARD_PUBNUB_PUB_KEY,
    authKey: joinRes.pubnub_token,
    userId: String(myUserId)
  });

  pubnub.addListener({
    status: (statusEvent) => {
      console.log(`PubNub Status: ${statusEvent.category} - ${statusEvent.operation}`);
    },
    message: (messageEvent) => {
      console.log(`\n[MESSAGE on ${messageEvent.channel}]:`, messageEvent.message);
    }
  });

  const subChannels = [channelName, `channel_all.${channelName}`];
  console.log(`Subscribing to: ${subChannels.join(', ')}`);
  pubnub.subscribe({ channels: subChannels });

  console.log('Listening for 20 seconds...');
  await new Promise(resolve => setTimeout(resolve, 20000));

  console.log('Unsubscribing...');
  pubnub.unsubscribeAll();

  console.log('Leaving channel...');
  await fetch(`http://127.0.0.1:8000/api/clubhouse/channels/${channelName}/leave`, {
    method: 'POST'
  });
  console.log('Done.');
}

main().catch(console.error);
