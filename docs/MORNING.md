# Good morning: Cowork is ready

## 1. What was done overnight
- **Your 6 bugs are fixed:** answers in whole sentences ("No, I meant to say open Microsoft Word."), Recent tasks
  keeps past conversations, the right column stays closed until you open it, Cowork can act on requests inside
  the app in front (reply, forward, mark unread, print...), the mode button shows where it goes, and question
  cards never say "Saying it aloud" with speaking off. Panels are 5% more see-through (contrast checked).
- **One start command:** `scripts/start.sh` checks everything in plain English, then starts Cowork.
- **Stop everything:** say "stop" or "cancel", or press Esc twice.
- **Demo backup:** Cmd+1, Cmd+2, Cmd+3 play the three demo clips.
- **Results view** in the app (Help > Results, or Cmd+K "results"), and `docs/chart.png` for the README.
- **Teach me** (Your voice > Phrases > Teach) and **dictation editing** ("comma", "period", "delete that"; numbers,
  times and dates written sensibly and checked first).
- **Tested like a tough judge** with the real Bonsai: fixed "open Safari" being taken for "new tab", kind refusals
  ("turn off wifi", "delete all my files", "book me a flight"), mumbling, and text on screen trying to give orders.
- **Ready for GitHub:** README for judges, MIT LICENSE, requirements.txt, repo cleaned, personal-data scan done,
  `cowork` merged into `main` on this Mac (not pushed). All 26 test files pass, also in a fresh copy of the repo.
- Details of everything: `docs/OVERNIGHT_LOG.md`.

## 2. How to start for the recording
1. Wi-Fi off. LM Studio open with Bonsai loaded and the server on. Quit other apps.
2. In Terminal:

       cd ~/Desktop/second-voice && scripts/start.sh --fresh

3. Do one throw-away request ("scroll down"), then click New task. Now follow `docs/video_script.md`.

## 3. Please check these by hand first (I couldn't: your screen was locked all night)
With the screen locked, macOS lets no app come to the front, so nothing that types or presses keys in another app
could be tried live. Please try each once (2 minutes):
- [ ] "open Mail", "open my latest email", "reply to this email": a reply opens, cursor in the message. Close it
      without sending (Cmd+W, Don't Save).
- [ ] "forward this" (close it the same way), "mark it as unread" (say it again to mark it read).
- [ ] In Word: "new blank document", dictation on, Cmd+1: the sentence is typed. Then "period" and "delete that".
- [ ] In Word: "print this": the print window opens (press Cancel).
- [ ] Hands: raise your right hand on a "Did you mean" card (the bar fills, then Yes); left hand = No; thumbs on
      the 4-option grid; the small camera picture inside the card.
- [ ] Conference mode: a confirmed sentence is spoken (TTSKit voice), quick phrase by number key and by thumb.
- [ ] Mini window: a question it can't say aloud makes one soft chime and the window glows.
- [ ] Esc twice stops everything; Cmd+1/2/3 play the clips.

## 4. Known issues and how to avoid them in the demo
- The first request after starting (or after it learns something) can take 4 to 6 seconds while Bonsai reads its
  instructions. Do one throw-away request before recording.
- A brand-new sentence in Conference mode takes about 4 to 6 seconds ("Preparing voice..."). The demo clips and
  your phrases are made in advance, so they play at once.
- Hands-free listening (in the corner window) also hears other people in the room. Keep the room quiet, or say
  "stop listening".
- Cmd+1/2/3 only work while Cowork's full window is in front ("show Cowork" brings it back).
- Don't show "send": it asks, and then refuses on purpose (sending is switched off).
- With speaking on, "read out my latest email" usually stays silent (an email is always a new sentence and the voice
  needs more than 2 seconds); with speaking off it opens the email instead.
- "Teach me" helps most when your voice comes out the same way each time; TORGO's speaker came out very
  differently each time, so teaching one recording didn't make his other recording instant.

## 5. The video
Script with timings, what to say and what to click: `docs/video_script.md`.

## 6. Putting it on GitHub (for a beginner)
1. On github.com: click "+" (top right) > "New repository". Name it `cowork`. Public. Do NOT tick "Add a README"
   or a license (the project already has them). Click "Create repository".
2. In Terminal (replace YOUR-NAME with your GitHub user name):

       cd ~/Desktop/second-voice
       git checkout main
       git remote add origin https://github.com/YOUR-NAME/cowork.git
       git push -u origin main

   If it asks you to log in, the easiest way is: `brew install gh`, then `gh auth login` (choose GitHub.com,
   HTTPS, log in with a browser), then run `git push -u origin main` again.
3. Put your video link into README.md (replace VIDEO_LINK_HERE), then:

       git commit -am "Add the demo video link" && git push

4. Final check that nothing personal is included (this should print nothing):

       git ls-files | grep -E "contacts.json|/profile.json|cowork_profile.json|tasks.json|datasets/|cache/|logs/|\.wav|\.m4a"

**Please decide:** every commit lists your email address (samarthkambli.sk@gmail.com) as its author, and GitHub
will show it. It is never inside a file. History was not rewritten (as asked). If you'd rather hide it in future
commits, GitHub > Settings > Emails > "Keep my email addresses private" gives you a no-reply address to use.

Also: `docs/OVERNIGHT_SPEC.md` (your instructions for tonight) is not committed. It mentions you were asleep and
your grandfather; add it only if you want it public.
