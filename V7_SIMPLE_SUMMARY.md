# Edu-LLM v7: The "Explain Like I'm 5" Summary

> A simple, plain-English summary of what changed in v7, why we did it, and how it works. No complex jargon!

---

## 1. The Short Version (What changed?)
In version 6, our chatbot was just a "messenger" — you asked a question, and it blindly forwarded it to the AI. 
In **version 7**, we upgraded it into a **Smart Teaching Assistant**. It can now read the teacher's PDFs, search for relevant course materials, and actively decide *how* to answer based on how much effort the student puts into their question. Most importantly: **it will never just give away the answers.**

---

## 2. The Main Features: What, Why, and How

### A. Reading Teacher's Documents (The Background Worker)
* **What we did:** Teachers can now upload course PDFs. The system automatically reads them, chops them into small paragraphs, and extracts the exercises.
* **Why we did it:** The AI cannot read an entire 50-page PDF every time a student asks a single question — it would be too slow and use too much memory. 
* **How we did it:** We created a "Background Worker". It's a quiet, invisible assistant that waits until the server's graphics card (GPU) is completely free. When no students are chatting, it slowly chops up the PDFs and saves them. This guarantees the chat never lags for active users.

### B. Smart Search (RAG & pgvector)
* **What we did:** When a student asks a question (like "What is multithreading?"), the system instantly finds the 3 most relevant paragraphs from the teacher's PDFs to help answer it.
* **Why we did it:** To give accurate answers based *only* on the specific class materials, and to show students exactly where the information came from.
* **How we did it:** We used a database called **pgvector**. It turns the chopped text paragraphs into math numbers (called "vectors"). When a student asks a question, the system finds the text numbers that mathematically match their question the closest. Also, we added strict rules so the search *never* pulls up the hidden solutions to exercises.

### C. The "Brain" (LangGraph Agent)
* **What we did:** We gave the chatbot a step-by-step thinking process instead of letting it answer immediately.
* **Why we did it:** To enforce strict teaching rules and make sure the AI knows when to search the database versus when to just chat normally.
* **How we did it:** We used a tool called **LangGraph** to build a flowchart. Now, the AI follows a strict path: `Figure out what the student wants` → `Search the database (if needed)` → `Apply teacher's rules` → `Finally, talk to the student`. 

### D. The "Anti-Cheat" Coach (Prompt Literacy)
* **What we did:** The system now judges if a student is trying hard or just begging for answers.
* **Why we did it:** Our true goal is to teach students *how to ask good questions*, not to do their homework for them.
* **How we did it:** We added simple checks to the student's message. If they just say "Give me the answer to Q2" (lazy), the AI switches to a strict "Socratic Mode" where it only asks guiding questions and refuses to give the answer. If they say "I tried X but got error Y" (hard-working), the AI helps them deeply. We track this "effort score" over time for each student!

---

## 3. Example Scenario
Imagine a student asks: *"Just give me the answer to Exercise 2."*
1. **The Brain** looks at the message and detects the student is putting in zero effort.
2. **The Brain** searches the database for "Exercise 2" but is strictly blocked from seeing the solution.
3. **The Brain** tells the AI: *"This student is being lazy. Act like Socrates. Do not give the answer, just give them a tiny hint."*
4. **The AI** replies: *"I can't give you the answer, but let's look at it together. What did you try so far?"*
