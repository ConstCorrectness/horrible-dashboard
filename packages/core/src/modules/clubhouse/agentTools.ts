/**
 * Agent tool definitions for the Clubhouse module.
 *
 * Exposes full query and control capabilities over the connected Clubhouse account,
 * live audio rooms, user profiles, following list, chat, and persistent people memory.
 */
import type { AgentToolDecl } from '../../registry';
import {
  addPersonNote,
  disconnectClubhouse,
  forgetPersonMemory,
  getClubhouseChannelChat,
  getClubhouseChannelDetails,
  getClubhouseChannels,
  getClubhouseFollowing,
  getClubhouseStatus,
  getClubhouseUserProfile,
  listPeopleMemory,
  searchClubhouseUsers,
  sendChannelMessage,
} from './api';

export const clubhouseAgentTools: AgentToolDecl[] = [
  {
    name: 'clubhouse.status',
    description: 'Read the connected Clubhouse account status (name, username, user ID, connected state).',
    sideEffect: false,
    handler: async () => {
      const status = await getClubhouseStatus();
      return status;
    },
  },
  {
    name: 'clubhouse.listRooms',
    description: 'List all currently active live Clubhouse audio rooms with topics, channels, active speakers, and member counts.',
    sideEffect: false,
    handler: async () => {
      const res = await getClubhouseChannels();
      const rooms = res.channels || [];
      return { count: rooms.length, rooms };
    },
  },
  {
    name: 'clubhouse.getChannelDetails',
    description: 'Fetch detailed information about a specific Clubhouse room / channel, including who is on stage, in the audience, and room topic.',
    params: {
      type: 'object',
      properties: {
        channel: { type: 'string', description: 'The unique channel identifier / room ID' },
      },
      required: ['channel'],
    },
    sideEffect: false,
    handler: async (args: Record<string, unknown>) => {
      const channel = String(args.channel || '');
      if (!channel) return { error: 'channel argument is required' };
      const details = await getClubhouseChannelDetails(channel);
      return details;
    },
  },
  {
    name: 'clubhouse.getUserProfile',
    description: 'Query a Clubhouse user profile by their numeric user_id. Returns full bio, display name, username, Twitter/Instagram handles, follower count, and clubs.',
    params: {
      type: 'object',
      properties: {
        userId: { type: 'number', description: 'The numeric user ID of the Clubhouse user' },
      },
      required: ['userId'],
    },
    sideEffect: false,
    handler: async (args: Record<string, unknown>) => {
      const userId = Number(args.userId);
      if (!userId || isNaN(userId)) return { error: 'Valid numeric userId is required' };
      const profile = await getClubhouseUserProfile(userId);
      return profile;
    },
  },
  {
    name: 'clubhouse.searchUsers',
    description: 'Search for Clubhouse users by display name, username, or topic keywords.',
    params: {
      type: 'object',
      properties: {
        query: { type: 'string', description: 'Name or username search query' },
      },
      required: ['query'],
    },
    sideEffect: false,
    handler: async (args: Record<string, unknown>) => {
      const query = String(args.query || '');
      if (!query.trim()) return { error: 'Search query cannot be empty' };
      const res = await searchClubhouseUsers(query);
      return { count: res.users.length, users: res.users };
    },
  },
  {
    name: 'clubhouse.getFollowing',
    description: 'Get the list of Clubhouse users currently followed by the connected account.',
    sideEffect: false,
    handler: async () => {
      const res = await getClubhouseFollowing();
      const following = res.users || [];
      return { count: following.length, following };
    },
  },
  {
    name: 'clubhouse.getChat',
    description: 'Read the recent live text comments and chat messages in an active Clubhouse channel.',
    params: {
      type: 'object',
      properties: {
        channel: { type: 'string', description: 'The room channel ID' },
      },
      required: ['channel'],
    },
    sideEffect: false,
    handler: async (args: Record<string, unknown>) => {
      const channel = String(args.channel || '');
      if (!channel) return { error: 'channel argument is required' };
      const res = await getClubhouseChannelChat(channel);
      return { count: res.comments.length, comments: res.comments };
    },
  },
  {
    name: 'clubhouse.sendMessage',
    description: 'Post a text chat message into a live Clubhouse audio room.',
    params: {
      type: 'object',
      properties: {
        channel: { type: 'string', description: 'The room channel ID' },
        message: { type: 'string', description: 'Text message to post into the room chat' },
      },
      required: ['channel', 'message'],
    },
    sideEffect: true,
    handler: async (args: Record<string, unknown>) => {
      const channel = String(args.channel || '');
      const message = String(args.message || '');
      if (!channel || !message.trim()) return { error: 'channel and message are required' };
      const res = await sendChannelMessage(channel, message);
      return res;
    },
  },
  {
    name: 'clubhouse.listPeopleMemory',
    description: 'Search or list persistent learned user memories, background notes, and tags stored by the voice agent.',
    params: {
      type: 'object',
      properties: {
        query: { type: 'string', description: 'Optional name, username, or topic keyword filter' },
      },
    },
    sideEffect: false,
    handler: async (args: Record<string, unknown>) => {
      const query = args.query ? String(args.query) : '';
      const list = await listPeopleMemory(query);
      return { count: list.length, people: list };
    },
  },
  {
    name: 'clubhouse.rememberUserFact',
    description: 'Save a learned note or fact about a Clubhouse user into long-term agent memory.',
    params: {
      type: 'object',
      properties: {
        userId: { type: 'number', description: 'The numeric user ID of the person' },
        note: { type: 'string', description: 'Fact or note to remember about this person' },
      },
      required: ['userId', 'note'],
    },
    sideEffect: true,
    handler: async (args: Record<string, unknown>) => {
      const userId = Number(args.userId);
      const note = String(args.note || '');
      if (!userId || !note.trim()) return { error: 'userId and note are required' };
      const updated = await addPersonNote(userId, note.trim());
      return { success: true, person: updated };
    },
  },
  {
    name: 'clubhouse.forgetUserMemory',
    description: 'Erase all persistent memories and learned notes for a given Clubhouse user ID.',
    params: {
      type: 'object',
      properties: {
        userId: { type: 'number', description: 'The numeric user ID of the person' },
      },
      required: ['userId'],
    },
    sideEffect: true,
    handler: async (args: Record<string, unknown>) => {
      const userId = Number(args.userId);
      if (!userId) return { error: 'userId is required' };
      const res = await forgetPersonMemory(userId);
      return res;
    },
  },
  {
    name: 'clubhouse.disconnect',
    description: 'Disconnect the connected Clubhouse account (clears the server-side token).',
    sideEffect: true,
    handler: async () => disconnectClubhouse(),
  },
];
