import React, { useState, useRef, useEffect } from 'react';
import SessionSidebar from '../components/SessionSidebar';
import './Chat.css';

const Chat = () => {
  const [currentSessionId, setCurrentSessionId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [inputText, setInputText] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const messagesEndRef = useRef(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const loadSession = async (sessionId) => {
    setCurrentSessionId(sessionId);
    setLoading(true);
    setError(null);
    try {
      const apiUrl = import.meta.env?.VITE_API_BASE_URL || 'http://localhost:8000';
      const token = localStorage.getItem('token');
      const response = await fetch(`${apiUrl}/api/student/sessions/${sessionId}`, {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      if (response.ok) {
        const data = await response.json();
        setMessages(data.messages || []);
      }
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const handleSendMessage = async (e) => {
    e.preventDefault();
    if (!inputText.trim() || !currentSessionId) return;

    const userMessage = { id: Date.now().toString(), role: 'user', content: inputText };
    setMessages(prev => [...prev, userMessage]);
    setInputText('');
    setLoading(true);
    setError(null);

    const botMessageId = (Date.now() + 1).toString();
    setMessages(prev => [...prev, { id: botMessageId, role: 'assistant', content: '' }]);

    try {
      const apiUrl = import.meta.env?.VITE_API_BASE_URL || 'http://localhost:8000';
      const token = localStorage.getItem('token');
      
      const response = await fetch(`${apiUrl}/api/chat/stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({ session_id: currentSessionId, message: userMessage.content })
      });

      if (!response.ok) {
        if (response.status === 429) {
          setError("Rate limit reached");
        } else {
          setError("An error occurred");
        }
        setMessages(prev => prev.filter(msg => msg.id !== botMessageId));
        return;
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");

      let buffer = '';

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        
        buffer += decoder.decode(value, { stream: true });
        let eventEndIndex;
        while ((eventEndIndex = buffer.indexOf('\n\n')) !== -1) {
          const event = buffer.slice(0, eventEndIndex);
          buffer = buffer.slice(eventEndIndex + 2);
          
          if (event.startsWith('data: ')) {
            const dataStr = event.substring(6);
            if (dataStr.trim() === '[DONE]') break;
            
            let token = dataStr;
            try {
              const parsed = JSON.parse(dataStr);
              if (parsed.token !== undefined) {
                token = parsed.token;
              }
            } catch (e) {
              // Raw text fallback
            }
            
            setMessages(prev => prev.map(msg => 
              msg.id === botMessageId 
                ? { ...msg, content: msg.content + token }
                : msg
            ));
          }
        }
      }
    } catch (err) {
      console.error(err);
      setError("Network error");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="chat-layout">
      <SessionSidebar onSelectSession={loadSession} />
      
      <div className="chat-main">
        {error && <div className="error-banner">{error}</div>}
        
        <div className="messages-container">
          {!currentSessionId ? (
            <div className="placeholder-text">Select or start a new chat</div>
          ) : messages.length === 0 ? (
            <div className="placeholder-text">Say hello!</div>
          ) : (
            messages.map((msg) => (
              <div key={msg.id} className={`message-wrapper ${msg.role}`}>
                <div className="message-bubble">
                  {msg.content}
                </div>
              </div>
            ))
          )}
          <div ref={messagesEndRef} />
        </div>

        <div className="input-container">
          <form onSubmit={handleSendMessage} className="message-form">
            <input
              type="text"
              placeholder="Type your message..."
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              disabled={loading || !currentSessionId}
            />
            <button type="submit" disabled={loading || !currentSessionId || !inputText.trim()}>
              Send
            </button>
          </form>
        </div>
      </div>
    </div>
  );
};

export default Chat;
