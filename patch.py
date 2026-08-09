with open("packages/core/src/modules/clubhouse/RoomsPanel.tsx", "r") as f:
    lines = f.readlines()

new_lines = []
for i, line in enumerate(lines):
    # Remove the bad lines around line 84-90
    if 86 <= i <= 95:
        if "isCurrentUserSpeaker" in line or "useEffect" in line or "agentEnabled" in line or "raiseHand" in line or "useMemo" in line or "return liveMe" in line or "amISpeaker" in line or "liveMe =" in line or "activeRoomInfo" in line:
            continue
        if "Audio analyzer reference for agent interruptd" in line:
            new_lines.append("  // Handlers for extended Clubhouse functionality\n")
            continue
    new_lines.append(line)

lines = new_lines
new_lines = []
for i, line in enumerate(lines):
    if "  // Audio analyzer reference for agent interrupt" in line:
        new_lines.append("""  const isCurrentUserSpeaker = useMemo(() => {
    const amISpeaker = activeRoomInfo?.users.find((u) => u.user_id === myUserId)?.is_speaker;
    const liveMe = liveUsers.find(u => u.userId === myUserId);
    return liveMe?.isSpeaker || amISpeaker || false;
  }, [activeRoomInfo, liveUsers, myUserId]);

  useEffect(() => {
    if (agentEnabled && !isCurrentUserSpeaker && !handRaised) {
      raiseHand(true);
    }
  }, [agentEnabled, isCurrentUserSpeaker, handRaised, raiseHand]);

""")
    new_lines.append(line)

with open("packages/core/src/modules/clubhouse/RoomsPanel.tsx", "w") as f:
    f.writelines(new_lines)

