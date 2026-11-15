# Demo video script (2 minutes)

## Before you press record
1. Wi-Fi off (click the Wi-Fi icon in the menu bar so it shows off; keep the menu bar visible in the video).
2. LM Studio open, Bonsai loaded, server on. Quit every other app (Word and Mail closed; Mail should have an
   email in the Inbox).
3. In Terminal: `cd ~/Desktop/second-voice && scripts/start.sh --fresh`
   (`--fresh` forgets rehearsals, so clip 2 asks "Did you mean" again). Wait for "All set".
4. Do one throw-away request first, e.g. type "scroll down" and press Enter, so Bonsai is warm (the very first
   request can take 4 to 6 seconds). Then click "New task".
5. Sit in front of the camera with good light on your hands.

## The script

| Time | What you do | What you say (short) |
|---|---|---|
| 0:00-0:10 | Cowork's start screen. Point at the Wi-Fi icon (off). | "My grandfather had Parkinson's. As his speech faded, his computer stopped understanding him. This is Cowork: it runs entirely on this Mac, Wi-Fi off." |
| 0:10-0:20 | Click the mic, say "open Mail". Cowork opens Mail and shrinks to the small window in the corner. | "Open Mail." |
| 0:20-0:30 | Say "open my latest email", then "reply to this email". The reply opens with the cursor in the message. | "It follows along in the corner, and never takes my keyboard." |
| 0:30-0:40 | Say "open Word": Word opens with a new blank document, ready to write in. Then say "show Cowork" to bring the full window back. | "Open Word." |
| 0:40-0:50 | Click the keyboard icon so dictation is on (green dot, Word). Press Cmd+1: clip 1 plays out loud and is typed into Word. | "Now the voice of a man with cerebral palsy, from the TORGO research recordings. WhisperKit heard 'But humanity is overwhelming'. Cowork typed what he meant." |
| 0:50-1:05 | Press Cmd+2: "Did you mean: We gathered shells on the beach?" Raise your RIGHT hand and hold it until the bar fills. It is typed into Word. | "When it isn't sure, it asks. He can answer with a hand." |
| 1:05-1:15 | Press Cmd+3 (the same recording again): typed at once, "Recognised instantly". | "It learned. Next time, no question at all." |
| 1:15-1:40 | Click the people icon (Switch to Conference mode). Press Cmd+4 (demo clip 4, a sentence not used before): WhisperKit hears "Could grandpa like to be a model in his language?", the card asks "Did you mean: Grandfather likes to be modern in his language?". Answer Yes (Enter or right hand): it is spoken aloud at once. Then press 2 for "I agree." | "In a meeting, Cowork speaks for him, in a natural voice, only after he confirms." |
| 1:40-1:55 | Press Cmd+K, type "results", Enter. The Results view: 8% vs 70%, 0% confidently wrong. | "WhisperKit alone got 8% exactly right. Cowork reached the right meaning 70% of the time, and was never confidently wrong." |
| 1:55-2:00 | Close the Results view. | "Bonsai and Argmax, fully offline. Cowork." |

## If something goes wrong while recording
- A wrong guess: say "no" (or press Esc), it offers other options; or say "stop".
- Anything stuck: press Esc twice (stops everything), then "New task".
- The window is in the corner and you need the full one: say "show Cowork", or click the expand icon.
- Cmd+1 to Cmd+4 only work while Cowork's full window is in front.
