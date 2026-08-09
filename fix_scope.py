with open("packages/core/src/modules/clubhouse/RoomsPanel.tsx", "r") as f:
    lines = f.readlines()

out = []
i = 0
block_lines = []
in_block = False
while i < len(lines):
    line = lines[i]
    if "const isCurrentUserSpeaker = useMemo(() => {" in line:
        in_block = True
    
    if in_block:
        if "  }, [agentEnabled, isCurrentUserSpeaker, handRaised, raiseHand]);" in line:
            in_block = False
            i += 1
            continue
        i += 1
        continue
    
    out.append(line)
    i += 1

# Now insert it around line 430
for i, line in enumerate(out):
    if "  // Audio analyzer reference for agent interruptd" in line or "  // Audio analyzer reference for agent interrupt" in line:
        out.insert(i, """  const isCurrentUserSpeaker = useMemo(() => {
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
        break

# fix the useMemo import just in case
for i, line in enumerate(out):
    if "import React, { useState, useEffect, useRef } from 'react';" in line:
        out[i] = "import React, { useState, useEffect, useRef, useMemo } from 'react';\n"
        break

with open("packages/core/src/modules/clubhouse/RoomsPanel.tsx", "w") as f:
    f.writelines(out)

