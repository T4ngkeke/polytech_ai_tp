import React, { useState, useEffect } from 'react';
import './Admin.css';

const Admin = () => {
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchUsers();
  }, []);

  const getAuthHeaders = () => {
    const token = localStorage.getItem('token');
    return {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    };
  };

  const getApiUrl = () => import.meta.env?.VITE_API_BASE_URL || 'http://localhost:8000';

  const fetchUsers = async () => {
    try {
      const response = await fetch(`${getApiUrl()}/api/admin/users`, { headers: getAuthHeaders() });
      if (response.ok) {
        setUsers(await response.json());
      }
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const updateQuota = async (userId, newQuota) => {
    try {
      const response = await fetch(`${getApiUrl()}/api/admin/users/${userId}/quota`, {
        method: 'PUT',
        headers: getAuthHeaders(),
        body: JSON.stringify({ daily_token_quota: parseInt(newQuota, 10) })
      });
      if (response.ok) {
        fetchUsers();
      }
    } catch (e) {
      console.error(e);
    }
  };

  const deleteUser = async (userId) => {
    if (window.confirm("Are you sure you want to delete this user?")) {
      try {
        const response = await fetch(`${getApiUrl()}/api/admin/users/${userId}`, {
          method: 'DELETE',
          headers: getAuthHeaders()
        });
        if (response.ok) {
          fetchUsers();
        }
      } catch (e) {
        console.error(e);
      }
    }
  };

  const handleQuotaChange = (userId, value) => {
    setUsers(users.map(u => u.id === userId ? { ...u, new_quota: value } : u));
  };

  return (
    <div className="admin-dashboard">
      <div className="dashboard-header">
        <h2>Admin Dashboard</h2>
      </div>

      <div className="admin-content">
        <div className="admin-card">
          <h3>User Management</h3>
          
          {loading ? (
            <div className="loading-text">Loading users...</div>
          ) : (
            <div className="table-responsive">
              <table className="users-table">
                <thead>
                  <tr>
                    <th>Username</th>
                    <th>Role</th>
                    <th>Daily Quota</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {users.map(user => (
                    <tr key={user.id}>
                      <td>{user.username}</td>
                      <td>
                        <span className={`role-badge ${user.role}`}>{user.role}</span>
                      </td>
                      <td>
                        <div className="quota-edit">
                          <input 
                            type="number" 
                            value={user.new_quota !== undefined ? user.new_quota : user.daily_token_quota}
                            onChange={(e) => handleQuotaChange(user.id, e.target.value)}
                            min="0"
                          />
                          <button 
                            className="save-btn"
                            onClick={() => updateQuota(user.id, user.new_quota !== undefined ? user.new_quota : user.daily_token_quota)}
                          >
                            Save
                          </button>
                        </div>
                      </td>
                      <td>
                        <button 
                          className="delete-btn"
                          onClick={() => deleteUser(user.id)}
                        >
                          Delete
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default Admin;
