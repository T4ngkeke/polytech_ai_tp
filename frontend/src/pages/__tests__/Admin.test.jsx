import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import Admin from '../Admin';

global.fetch = vi.fn();
// Mock window.confirm
global.confirm = vi.fn();

describe('Admin Component', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  const setupMocks = () => {
    fetch.mockImplementation((url, options) => {
      if (url.includes('/api/admin/users') && (!options || !options.method || options.method === 'GET')) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve([
            { id: 'user-1', username: 'student1', role: 'student', daily_token_quota: 1000 },
            { id: 'user-2', username: 'teacher1', role: 'teacher', daily_token_quota: 5000 }
          ])
        });
      }
      if (url.includes('/quota')) {
        return Promise.resolve({ ok: true });
      }
      if (options && options.method === 'DELETE') {
        return Promise.resolve({ ok: true });
      }
      return Promise.resolve({ ok: false });
    });
  };

  it('renders user table from mocked API', async () => {
    setupMocks();
    render(<Admin />);
    
    await waitFor(() => {
      expect(screen.getByText('student1')).toBeInTheDocument();
      expect(screen.getByText('teacher1')).toBeInTheDocument();
      expect(screen.getByDisplayValue('1000')).toBeInTheDocument();
      expect(screen.getByDisplayValue('5000')).toBeInTheDocument();
    });
  });

  it('save button calls quota update endpoint with correct value', async () => {
    setupMocks();
    render(<Admin />);
    
    await waitFor(() => {
      expect(screen.getByDisplayValue('1000')).toBeInTheDocument();
    });

    const quotaInputs = screen.getAllByRole('spinbutton');
    fireEvent.change(quotaInputs[0], { target: { value: '2000' } });
    
    const saveButtons = screen.getAllByRole('button', { name: /save/i });
    fireEvent.click(saveButtons[0]);

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/admin/users/user-1/quota'),
        expect.objectContaining({
          method: 'PUT',
          body: JSON.stringify({ daily_token_quota: 2000 })
        })
      );
    });
  });

  it('delete button shows confirmation before calling delete endpoint', async () => {
    setupMocks();
    global.confirm.mockReturnValueOnce(true);

    render(<Admin />);
    
    await waitFor(() => {
      expect(screen.getByText('student1')).toBeInTheDocument();
    });

    const deleteButtons = screen.getAllByRole('button', { name: /delete/i });
    fireEvent.click(deleteButtons[0]);

    expect(global.confirm).toHaveBeenCalled();
    
    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/admin/users/user-1'),
        expect.objectContaining({ method: 'DELETE' })
      );
    });
  });
});
