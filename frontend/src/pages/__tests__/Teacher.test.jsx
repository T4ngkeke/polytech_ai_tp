import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import Teacher from '../Teacher';

global.fetch = vi.fn();

describe('Teacher Component', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  const setupMocks = () => {
    fetch.mockImplementation((url, options) => {
      if (url.includes('/api/teacher/students')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve([
            { id: 'student-1', username: 'student1', daily_token_usage: 150 },
            { id: 'student-2', username: 'student2', daily_token_usage: 50 }
          ])
        });
      }
      if (url.includes('/api/teacher/rules') && (!options || !options.method || options.method === 'GET')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve([
            { id: 'rule-1', target_student_id: 'student-1', is_active: true, rules_json: '{"test":true}' }
          ])
        });
      }
      if (url.includes('/api/teacher/sessions/student-1')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve([
            { id: 'msg-1', role: 'user', content: 'student message' }
          ])
        });
      }
      if (url.includes('/toggle')) {
        return Promise.resolve({ ok: true });
      }
      if (options && options.method === 'POST') {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ id: 'rule-2' }) });
      }
      return Promise.resolve({ ok: false });
    });
  };

  it('renders student list from mocked API', async () => {
    setupMocks();
    render(<Teacher />);
    
    await waitFor(() => {
      expect(screen.getByText('student1')).toBeInTheDocument();
      expect(screen.getByText('150 tokens today')).toBeInTheDocument();
    });
  });

  it('chat history renders correctly for selected student', async () => {
    setupMocks();
    render(<Teacher />);
    
    await waitFor(() => {
      expect(screen.getByText('student1')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('student1'));

    await waitFor(() => {
      expect(screen.getByText('student message')).toBeInTheDocument();
    });
  });

  it('toggle button calls the correct endpoint', async () => {
    setupMocks();
    render(<Teacher />);
    
    await waitFor(() => {
      expect(screen.getByText('{"test":true}')).toBeInTheDocument();
    });

    const toggleBtn = screen.getByRole('button', { name: /deactivate|activate/i });
    fireEvent.click(toggleBtn);

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/teacher/rules/rule-1/toggle'),
        expect.objectContaining({ method: 'PUT' })
      );
    });
  });

  it('new rule form submission calls POST /api/teacher/rules', async () => {
    setupMocks();
    render(<Teacher />);
    
    await waitFor(() => {
      expect(screen.getByLabelText(/Target Student ID/i)).toBeInTheDocument();
    });

    fireEvent.change(screen.getByLabelText(/Target Student ID/i), { target: { value: 'student-2' } });
    fireEvent.change(screen.getByLabelText(/Rules JSON/i), { target: { value: '{"key":"value"}' } });
    
    fireEvent.click(screen.getByRole('button', { name: /Create Rule/i }));

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/teacher/rules'),
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ target_student_id: 'student-2', rules_json: '{"key":"value"}' })
        })
      );
    });
  });
});
