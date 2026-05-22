import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import SessionSidebar from '../SessionSidebar';

// Mock fetch
global.fetch = vi.fn();

describe('SessionSidebar', () => {
  const mockOnSelectSession = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders list of sessions from mocked API response', async () => {
    fetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ([
        { id: '1', name: 'Chat 1', created_at: '2023-01-01T00:00:00Z' },
        { id: '2', name: 'Chat 2', created_at: '2023-01-02T00:00:00Z' }
      ])
    });

    render(<SessionSidebar onSelectSession={mockOnSelectSession} />);
    
    // Should show loading or directly resolve
    await waitFor(() => {
      expect(screen.getByText('Chat 1')).toBeInTheDocument();
      expect(screen.getByText('Chat 2')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Chat 1'));
    expect(mockOnSelectSession).toHaveBeenCalledWith('1');
  });

  it('clicking "New Chat" calls the create session endpoint', async () => {
    // Initial fetch for sessions
    fetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ([])
    });

    render(<SessionSidebar onSelectSession={mockOnSelectSession} />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /new chat/i })).toBeInTheDocument();
    });

    // Mock POST for new chat
    fetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ id: '3', name: 'New Session' })
    });

    fireEvent.click(screen.getByRole('button', { name: /new chat/i }));

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledTimes(2); // GET and then POST
      expect(fetch).toHaveBeenLastCalledWith(expect.stringContaining('/api/student/sessions'), expect.objectContaining({ method: 'POST' }));
      expect(mockOnSelectSession).toHaveBeenCalledWith('3');
    });
  });
});
