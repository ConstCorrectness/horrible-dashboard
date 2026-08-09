import re

with open("packages/core/src/modules/clubhouse/RoomsPanel.tsx", "r") as f:
    content = f.read()

# 1. Imports
content = content.replace(
    "inviteClubhouseSpeaker,",
    "inviteClubhouseSpeaker,\n  updateClubhouseTopic,\n  updateClubhouseHandraiseSettings,\n  updateClubhouseChatSettings,"
)

# 2. States
state_block = """  const [agentRespondsToVoice, setAgentRespondsToVoice] = useState(true);
  const [agentPromptPresets, setAgentPromptPresets] = useState<{name: string, prompt: string}[]>(() => {
    try { return JSON.parse(localStorage.getItem('agentPresets') || '[]'); } catch { return []; }
  });
  const [myUserId, setMyUserId] = useState<number | null>(null);

  // Room settings modal states
  const [showRoomSettingsModal, setShowRoomSettingsModal] = useState(false);
  const [settingTopic, setSettingTopic] = useState('');
  const [settingHandraiseEnabled, setSettingHandraiseEnabled] = useState(true);
  const [settingHandraisePermission, setSettingHandraisePermission] = useState<number>(1);
  const [settingChatEnabled, setSettingChatEnabled] = useState(true);
  const [savingSettings, setSavingSettings] = useState(false);"""
  
content = re.sub(
    r"const \[agentRespondsToChat.*?const \[myUserId, setMyUserId\] = useState<number \| null>\(null\);",
    state_block,
    content,
    flags=re.DOTALL
)

# 3. Hook extraction
content = content.replace(
    "playAgentAudio,\n    loading,",
    "playAgentAudio,\n    stopAgentAudio,\n    loading,"
)

# 4. Handlers
handlers_block = """const handleStartRoom = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreatingRoom(true);
    try {
      const isPrivate = newRoomPrivacy === 'private';
      const isSocialMode = newRoomPrivacy === 'social';
      const res = await createClubhouseChannel(newRoomTopic.trim(), isPrivate, isSocialMode);
      setShowStartRoomModal(false);
      setNewRoomTopic('');
      if (res.channel) {
        const roomInfo: Channel = { channel: res.channel, topic: newRoomTopic.trim() || 'My New Room', num_speakers: 1, num_all: 1, club: null, users: [] };
        setActiveRoomInfo(roomInfo);
        void joinRoom(res.channel, roomInfo.users);
      }
    } catch (err) {
      console.error('Failed to start room:', err);
      toastsStore.add('error', 'Failed to start room', String(err));
    } finally {
      setCreatingRoom(false);
    }
  };

  const handleOpenRoomSettings = () => {
    if (!activeRoomInfo) return;
    setSettingTopic(activeRoomInfo.topic || '');
    setSettingHandraiseEnabled(true);
    setSettingHandraisePermission(1);
    setSettingChatEnabled(true);
    setShowRoomSettingsModal(true);
  };

  const handleSaveRoomSettings = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!activeChannel) return;
    setSavingSettings(true);
    try {
      await updateClubhouseTopic(activeChannel, settingTopic.trim());
      await updateClubhouseHandraiseSettings(activeChannel, settingHandraiseEnabled, settingHandraisePermission);
      await updateClubhouseChatSettings(activeChannel, settingChatEnabled);
      toastsStore.add('success', 'Room Settings', 'Successfully updated room settings');
      setShowRoomSettingsModal(false);
      if (activeRoomInfo) setActiveRoomInfo({ ...activeRoomInfo, topic: settingTopic.trim() });
    } catch (err: any) {
      toastsStore.add('error', 'Update Failed', err.message || 'Could not update all settings');
    } finally {
      setSavingSettings(false);
    }
  };"""

content = re.sub(
    r"const handleStartRoom = async.*?setCreatingRoom\(false\);\n    }\n  };",
    handlers_block,
    content,
    flags=re.DOTALL
)

# 5. triggerAgentResponse intercept
trigger_old = """  const triggerAgentResponse = async (text: string, source: 'chat' | 'voice' = 'voice') => {
    if (!text || isAgentSpeaking) return;
    try {
      setIsAgentSpeaking(true);
      agentAbortControllerRef.current = new AbortController();"""
      
trigger_new = """  const triggerAgentResponse = async (text: string, source: 'chat' | 'voice' = 'voice') => {
    if (!text || isAgentSpeaking) return;
    try {
      setIsAgentSpeaking(true);

      const amISpeaker = currentRoom?.users.find((u) => u.user_id === myUserId)?.is_speaker;
      const liveMe = liveUsers.find(u => u.userId === myUserId);
      const isActuallySpeaker = liveMe?.isSpeaker || amISpeaker || false;

      // Handle custom /agent commands (Authorized users / mods only for settings)
      if (text.startsWith('/agent')) {
        const parts = text.trim().split(' ');
        const cmd = parts[1];
        if (cmd === 'topic' && activeChannel) {
          const amIMod = currentRoom?.users.find((u) => u.user_id === myUserId)?.is_moderator;
          if (amIMod) {
            const newTopic = parts.slice(2).join(' ');
            await updateClubhouseTopic(activeChannel, newTopic);
            await sendComment(`🤖 Room topic updated to: ${newTopic}`);
            if (activeRoomInfo) setActiveRoomInfo({ ...activeRoomInfo, topic: newTopic });
          } else {
            await sendComment(`🤖 Only moderators can change the topic.`);
          }
          setIsAgentSpeaking(false);
          return;
        } else if (cmd === 'search') {
           const query = parts.slice(2).join(' ');
           await sendComment(`🤖 Searching the web for: ${query}...`);
           try {
              text = `Please perform a simulated web search or provide factual knowledge about: ${query}`;
           } catch(e) {}
        }
      }

      agentAbortControllerRef.current = new AbortController();"""
content = content.replace(trigger_old, trigger_new)

# 6. Audio logic in triggerAgentResponse
audio_old = """      if (data.completion) {
        const text = data.completion.trim();
        await sendComment(`🤖 ${text}`);
        await playAgentAudio(text);
      }
      setIsAgentSpeaking(false);
    } catch (e) {"""

audio_new = """      if (data.completion) {
        const responseText = data.completion.trim();
        await sendComment(`🤖 ${responseText}`);
        if (isActuallySpeaker) {
          await playAgentAudio(responseText);
        }
      }
      setIsAgentSpeaking(false);
    } catch (e) {
      if (e instanceof Error && e.name === 'AbortError') return;"""

content = content.replace(audio_old, audio_new)

# 7. Agent panel replacements
panel_old = """                    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
                      <span style={{ fontSize: '0.75rem', color: '#94a3b8', fontWeight: 600 }}>System Prompt / Persona:</span>
                      <textarea 
                        value={agentSystemPrompt} 
                        onChange={(e) => setAgentSystemPrompt(e.target.value)} 
                        placeholder="System Prompt / Persona"
                        style={{ width: '100%', minHeight: '80px', padding: '0.75rem', fontSize: '0.8rem', background: '#1d2026', color: '#f1f5f9', border: '1px solid #2e333d', borderRadius: '8px', resize: 'vertical' }} 
                      />
                    </div>"""

panel_new = """                    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <span style={{ fontSize: '0.75rem', color: '#94a3b8', fontWeight: 600 }}>System Prompt / Persona:</span>
                        <div style={{ display: 'flex', gap: '0.5rem' }}>
                          <select 
                            onChange={(e) => { if (e.target.value) setAgentSystemPrompt(e.target.value); }}
                            style={{ padding: '0.2rem', fontSize: '0.7rem', background: '#1d2026', color: '#f1f5f9', border: '1px solid #2e333d' }}
                          >
                            <option value="">Load Preset...</option>
                            {agentPromptPresets.map((p, i) => (
                              <option key={i} value={p.prompt}>{p.name}</option>
                            ))}
                          </select>
                          <button 
                            className="ch-btn-action"
                            style={{ padding: '0.2rem 0.5rem', fontSize: '0.7rem' }}
                            onClick={() => {
                              const name = prompt('Name for this preset:');
                              if (name) {
                                const newPresets = [...agentPromptPresets, { name, prompt: agentSystemPrompt }];
                                setAgentPromptPresets(newPresets);
                                localStorage.setItem('agentPresets', JSON.stringify(newPresets));
                              }
                            }}
                          >
                            Save
                          </button>
                        </div>
                      </div>
                      <textarea 
                        value={agentSystemPrompt} 
                        onChange={(e) => setAgentSystemPrompt(e.target.value)} 
                        placeholder="System Prompt / Persona"
                        style={{ width: '100%', minHeight: '80px', padding: '0.75rem', fontSize: '0.8rem', background: '#1d2026', color: '#f1f5f9', border: '1px solid #2e333d', borderRadius: '8px', resize: 'vertical' }} 
                      />
                    </div>"""
content = content.replace(panel_old, panel_new)

# 8. Interrupt button
btn_old = """                    <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '0.5rem', borderTop: '1px solid rgba(255,255,255,0.05)', paddingTop: '1rem' }}>
                      <button
                        className="ch-btn-action"
                        style={{ padding: '0.6rem 1.2rem', fontSize: '0.85rem', background: '#2e333d', border: 'none', borderRadius: '20px', cursor: 'pointer' }}
                        onClick={() => triggerAgentResponse(" ")}
                        disabled={isAgentSpeaking || !isCurrentUserSpeaker}
                        title={!isCurrentUserSpeaker ? "Must be a speaker to use agent voice" : "Force speak"}
                      >
                        🗣️ Speak Now
                      </button>
                    </div>
                    {!isCurrentUserSpeaker && (
                      <p style={{ fontSize: '0.75rem', color: '#ef4444', margin: '0', textAlign: 'right' }}>
                        ⚠️ You must be on stage to use the Agent's voice.
                      </p>
                    )}"""

btn_new = """                    <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.5rem', marginTop: '0.5rem', borderTop: '1px solid rgba(255,255,255,0.05)', paddingTop: '1rem' }}>
                      <button
                        className="ch-btn-action"
                        style={{ padding: '0.6rem 1.2rem', fontSize: '0.85rem', background: '#e11d48', color: 'white', border: 'none', borderRadius: '20px', cursor: 'pointer' }}
                        onClick={stopAgentAudio}
                        disabled={!isAgentSpeaking}
                      >
                        ✋ Interrupt
                      </button>
                      <button
                        className="ch-btn-action"
                        style={{ padding: '0.6rem 1.2rem', fontSize: '0.85rem', background: '#2e333d', border: 'none', borderRadius: '20px', cursor: 'pointer' }}
                        onClick={() => triggerAgentResponse(" ")}
                        disabled={isAgentSpeaking}
                      >
                        🗣️ Speak Now
                      </button>
                    </div>
                    {!isCurrentUserSpeaker && (
                      <p style={{ fontSize: '0.75rem', color: '#94a3b8', margin: '0', textAlign: 'right' }}>
                        ℹ️ You are in the audience. Agent will respond in text chat only.
                      </p>
                    )}"""
content = content.replace(btn_old, btn_new)


# 9. Header settings button
hdr_old = """            </button>
          </div>

          <div className="ch-room-title-section">"""

hdr_new = """            </button>
            {(() => {
              const amIMod = currentRoom?.users.find((u) => u.user_id === myUserId)?.is_moderator;
              if (amIMod) {
                return (
                  <button
                    className="ch-btn-action"
                    style={{ padding: '0.4rem 0.8rem', borderRadius: '20px', fontSize: '0.75rem', fontWeight: 600, flex: 'none', background: 'rgba(255, 255, 255, 0.05)' }}
                    onClick={handleOpenRoomSettings}
                  >
                    ⚙ Settings
                  </button>
                );
              }
              return null;
            })()}
          </div>

          <div className="ch-room-title-section">"""
content = content.replace(hdr_old, hdr_new)

# 10. Room Settings Modal
modal = """          </div>
        )}

        {/* Room Settings Modal */}
        {showRoomSettingsModal && (
          <div className="ch-modal-overlay" onClick={() => setShowRoomSettingsModal(false)}>
            <div className="ch-modal-card ch-settings-modal" onClick={(e) => e.stopPropagation()}>
              <div className="ch-modal-header">
                <h3 className="ch-modal-title">Room Settings</h3>
                <button className="ch-modal-close" onClick={() => setShowRoomSettingsModal(false)}>✕</button>
              </div>
              <div className="ch-modal-body">
                <form onSubmit={handleSaveRoomSettings} className="ch-start-room-form">
                  <div className="ch-form-group">
                    <label>Room Topic</label>
                    <input type="text" placeholder="What is this room about?" value={settingTopic} onChange={(e) => setSettingTopic(e.target.value)} className="ch-input" />
                  </div>
                  <div className="ch-form-group">
                    <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                      <input type="checkbox" checked={settingHandraiseEnabled} onChange={(e) => setSettingHandraiseEnabled(e.target.checked)} /> Enable Hand Raising
                    </label>
                  </div>
                  {settingHandraiseEnabled && (
                    <div className="ch-form-group">
                      <label>Who can raise hands?</label>
                      <select value={settingHandraisePermission} onChange={(e) => setSettingHandraisePermission(Number(e.target.value))} className="ch-input" style={{ width: '100%' }}>
                        <option value={1}>Everyone</option>
                        <option value={2}>Followed by Speakers</option>
                      </select>
                    </div>
                  )}
                  <div className="ch-form-group">
                    <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                      <input type="checkbox" checked={settingChatEnabled} onChange={(e) => setSettingChatEnabled(e.target.checked)} /> Enable Room Chat
                    </label>
                  </div>
                  <button type="submit" className="ch-btn-submit" disabled={savingSettings}>{savingSettings ? 'Saving...' : 'Save Settings'}</button>
                </form>
              </div>
            </div>
          </div>
        )}
      </div>"""

content = content.replace("          </div>\n        )}\n      </div>", modal)

with open("packages/core/src/modules/clubhouse/RoomsPanel.tsx", "w") as f:
    f.write(content)
