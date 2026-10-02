(Use GPT with max reasoning / thinking. Attach 01_janus_context_brief.md + the participant guide.)

You are a creative product strategist and demo designer. Read the attached brief. Our team is competing in the Samsung PRISM GenAI Hackathon, Theme 05 (interruptible real-time voice agents). We already have a robust kernel for "safe-under-interruption" voice agents and a working camera-grounded device-troubleshooting extension. We need to make our extension feel native to the Samsung ecosystem and memorable to Samsung judges.

Generate ideas, divergently first, then converge:

A. 15 raw ideas for Samsung-ecosystem voice-agent scenarios where the user's interruption/correction is the hero of the story (not a footnote). Mix: home (SmartThings, Bespoke appliances, Family Hub), phone (Galaxy, One UI), wearables (Watch/Buds/Ring), car, health/care for elderly relatives, accessibility, India-specific life (power cuts, monsoon, shared families, festival cooking, regional languages). At least 4 should be weird or unexpected.
B. For each, one line: the "wow moment" a judge would see in the first 30 seconds, and the interruption that proves our safety claim (e.g. a correction that cancels a pending write, a user changing their mind while an action is mid-flight, a conflict between what the camera shows and what the user says).
C. 3 ideas that exploit OUR unique capabilities specifically: same-step cancellation of stale tool calls, write-once ledger, read-set invalidation, honest failure reporting, camera grounded evidence with conflicts, visible decision log. Make the demo VISUALLY show these (e.g. a live "what I cancelled and why" panel).
D. Pick your top 3 under the constraint: buildable in about 2 days on top of an existing tool-manifest (tools + small KB, mock backends acceptable), filmable in one unedited 3-minute take, and understandable by judges without explanation. Write for each a demo script with exact utterances, the interruption timing, the tools called (name, args, READ/WRITE), and a fallback if live APIs fail.
E. Naming and framing: a name and one-sentence pitch for the top pick, aligned with Samsung's "AI for All" / connected-living language, without claiming official Samsung integrations we do not have.
F. Critique yourself: for the top 3, what would a skeptical Samsung engineer find fake or shallow, and how do we pre-empt it honestly?

Be concrete, avoid generic "smart home assistant" ideas, and do not propose anything that needs Samsung partner access unless labelled "simulated".
