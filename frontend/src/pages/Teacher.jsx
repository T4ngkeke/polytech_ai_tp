import React, { useState, useEffect } from 'react';
import './Teacher.css';

const Teacher = () => {
  const [students, setStudents] = useState([]);
  const [rules, setRules] = useState([]);
  const [selectedStudentId, setSelectedStudentId] = useState(null);
  const [chatHistory, setChatHistory] = useState([]);
  const [newRuleTarget, setNewRuleTarget] = useState('');
  const [newRuleJson, setNewRuleJson] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    fetchStudents();
    fetchRules();
  }, []);

  useEffect(() => {
    if (selectedStudentId) {
      fetchChatHistory(selectedStudentId);
    } else {
      setChatHistory([]);
    }
  }, [selectedStudentId]);

  const getAuthHeaders = () => {
    const token = localStorage.getItem('token');
    return {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    };
  };

  const getApiUrl = () => import.meta.env?.VITE_API_BASE_URL || 'http://localhost:8000';

  const fetchStudents = async () => {
    try {
      const response = await fetch(`${getApiUrl()}/api/teacher/students`, { headers: getAuthHeaders() });
      if (response.ok) {
        setStudents(await response.json());
      }
    } catch (e) {
      console.error(e);
    }
  };

  const fetchRules = async () => {
    try {
      const response = await fetch(`${getApiUrl()}/api/teacher/rules`, { headers: getAuthHeaders() });
      if (response.ok) {
        setRules(await response.json());
      }
    } catch (e) {
      console.error(e);
    }
  };

  const fetchChatHistory = async (studentId) => {
    setLoading(true);
    try {
      const response = await fetch(`${getApiUrl()}/api/teacher/sessions/${studentId}`, { headers: getAuthHeaders() });
      if (response.ok) {
        setChatHistory(await response.json());
      }
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const toggleRule = async (ruleId) => {
    try {
      const response = await fetch(`${getApiUrl()}/api/teacher/rules/${ruleId}/toggle`, {
        method: 'PUT',
        headers: getAuthHeaders()
      });
      if (response.ok) {
        fetchRules();
      }
    } catch (e) {
      console.error(e);
    }
  };

  const handleCreateRule = async (e) => {
    e.preventDefault();
    try {
      const response = await fetch(`${getApiUrl()}/api/teacher/rules`, {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify({
          target_student_id: newRuleTarget || null,
          rules_json: newRuleJson
        })
      });
      if (response.ok) {
        setNewRuleTarget('');
        setNewRuleJson('');
        fetchRules();
      }
    } catch (e) {
      console.error(e);
    }
  };

  return (
    <div className="teacher-dashboard">
      <div className="dashboard-header">
        <h2>Teacher Dashboard</h2>
      </div>
      
      <div className="dashboard-content">
        <div className="section section-a">
          <h3>Students</h3>
          <div className="student-list">
            {students.map(student => (
              <div 
                key={student.id} 
                className={`student-card ${selectedStudentId === student.id ? 'active' : ''}`}
                onClick={() => setSelectedStudentId(student.id)}
              >
                <div className="student-name">{student.username}</div>
                <div className="student-usage">{student.daily_token_usage} tokens today</div>
              </div>
            ))}
          </div>
        </div>

        <div className="section section-b">
          <h3>Rule Management</h3>
          <form className="rule-form" onSubmit={handleCreateRule}>
            <div className="form-group">
              <label htmlFor="target-student">Target Student ID</label>
              <input 
                id="target-student"
                type="text" 
                value={newRuleTarget} 
                onChange={(e) => setNewRuleTarget(e.target.value)} 
                placeholder="Leave empty for global rule"
              />
            </div>
            <div className="form-group">
              <label htmlFor="rules-json">Rules JSON</label>
              <textarea 
                id="rules-json"
                value={newRuleJson} 
                onChange={(e) => setNewRuleJson(e.target.value)}
                required
                rows="4"
              ></textarea>
            </div>
            <button type="submit" className="primary-btn">Create Rule</button>
          </form>

          <div className="rules-list">
            {rules.map(rule => (
              <div key={rule.id} className="rule-card">
                <div className="rule-info">
                  <div>Target: {rule.target_student_id || 'Global'}</div>
                  <pre>{rule.rules_json}</pre>
                </div>
                <button 
                  className={`toggle-btn ${rule.is_active ? 'active' : 'inactive'}`}
                  onClick={() => toggleRule(rule.id)}
                >
                  {rule.is_active ? 'Deactivate' : 'Activate'}
                </button>
              </div>
            ))}
          </div>
        </div>

        <div className="section section-c">
          <h3>Chat History</h3>
          <div className="chat-history-container">
            {!selectedStudentId ? (
              <div className="placeholder-text">Select a student to view chat history</div>
            ) : loading ? (
              <div className="placeholder-text">Loading history...</div>
            ) : chatHistory.length === 0 ? (
              <div className="placeholder-text">No messages found</div>
            ) : (
              chatHistory.map(msg => (
                <div key={msg.id} className={`history-msg ${msg.role}`}>
                  <span className="msg-role">{msg.role}</span>
                  <div className="msg-content">{msg.content}</div>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default Teacher;
