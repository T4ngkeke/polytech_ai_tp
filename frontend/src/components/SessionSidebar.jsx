import React, { useState, useEffect } from 'react';
import './SessionSidebar.css';

const SessionSidebar = ({ onSelectSession }) => {
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchSessions();
  }, []);

  const fetchSessions = async () => {
    try {
      const apiUrl = import.meta.env?.VITE_API_BASE_URL || 'http://localhost:8000';
      const token = localStorage.getItem('token');
      const response = await fetch(`${apiUrl}/api/student/sessions`, {
        headers: {
          'Authorization': `Bearer ${token}`
        }
      });
      if (response.ok) {
        const data = await response.json();
        setSessions(data);
      }
    } catch (error) {
      console.error('Failed to fetch sessions', error);
    } finally {
      setLoading(false);
    }
  };

  const createNewChat = async () => {
    try {
      const apiUrl = import.meta.env?.VITE_API_BASE_URL || 'http://localhost:8000';
      const token = localStorage.getItem('token');
      const response = await fetch(`${apiUrl}/api/student/sessions`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({})
      });
      if (response.ok) {
        const newSession = await response.json();
        setSessions([newSession, ...sessions]);
        onSelectSession(newSession.id);
      }
    } catch (error) {
      console.error('Failed to create new chat', error);
    }
  };

  return (
    <div className="session-sidebar">
      <div className="sidebar-header">
        <button className="new-chat-btn" onClick={createNewChat}>
          + New Chat
        </button>
      </div>
      <div className="session-list">
        {loading ? (
          <div className="loading-text">Loading...</div>
        ) : sessions.length === 0 ? (
          <div className="empty-text">No chats yet</div>
        ) : (
          sessions.map((session) => (
            <div
              key={session.id}
              className="session-item"
              onClick={() => onSelectSession(session.id)}
            >
              <div className="session-name">{session.name}</div>
            </div>
          ))
        )}
      </div>
    </div>
  );
};

export default SessionSidebar;
