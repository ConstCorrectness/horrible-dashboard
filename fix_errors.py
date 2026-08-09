import re

with open("packages/core/src/modules/clubhouse/RoomsPanel.tsx", "r") as f:
    content = f.read()

# 1. Add agentRespondsToChat back
state_block = """  const [agentRespondsToVoice, setAgentRespondsToVoice] = useState(true);
  const [agentRespondsToChat, setAgentRespondsToChat] = useState(false);
  const [agentPromptPresets, setAgentPromptPresets] = useState<{name: string, prompt: string}[]>(() => {"""
content = content.replace("  const [agentRespondsToVoice, setAgentRespondsToVoice] = useState(true);\n  const [agentPromptPresets, setAgentPromptPresets] = useState<{name: string, prompt: string}[]>(() => {", state_block)

# 2. currentRoom -> activeRoomInfo in triggerAgentResponse
content = content.replace("currentRoom?.users.find", "activeRoomInfo?.users.find")

# 3. liveUsers.get -> liveUsers.find
content = content.replace("liveUsers.get(selectedUser.user_id)?.isSpeaker", "liveUsers.find((u: any) => u.userId === selectedUser.user_id)?.isSpeaker")

with open("packages/core/src/modules/clubhouse/RoomsPanel.tsx", "w") as f:
    f.write(content)

with open("packages/core/src/modules/clubhouse/useClubhouseVoice.ts", "r") as f:
    voice_content = f.read()

# 4. Fix type error in useClubhouseVoice.ts:576
voice_content = voice_content.replace(
    "id: c.message_id || c.time_created,",
    "id: String(c.message_id || c.time_created),"
)
with open("packages/core/src/modules/clubhouse/useClubhouseVoice.ts", "w") as f:
    f.write(voice_content)
