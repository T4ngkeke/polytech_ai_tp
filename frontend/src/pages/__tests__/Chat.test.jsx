import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import Chat from '../Chat';

// Mock SessionSidebar
vi.mock('../../components/SessionSidebar', () => {
  return {
    default: ({ onSelectSession }) => (
      <div data-testid="sidebar">
        <button onClick={() => onSelectSession('session-123')}>Select Session 123</button>
      </div>
    ),
  };
});

// Mock fetch
global.fetch = vi.fn();

describe('Chat Component', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders message history from mocked session fetch', async () => {
    fetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        id: 'session-123',
        messages: [
          { id: '1', role: 'user', content: 'Hello' },
          { id: '2', role: 'assistant', content: 'Hi there' }
        ]
      })
    });

    render(<Chat />);
    
    fireEvent.click(screen.getByText('Select Session 123'));

    await waitFor(() => {
      expect(screen.getByText('Hello')).toBeInTheDocument();
      expect(screen.getByText('Hi there')).toBeInTheDocument();
    });
  });

  it('shows "Rate limit reached" banner on 429', async () => {
    // Setup selected session first
    fetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ id: 'session-123', messages: [] })
    });

    render(<Chat />);
    fireEvent.click(screen.getByText('Select Session 123'));
    
    await waitFor(() => {
      expect(screen.getByPlaceholderText(/type your message/i)).toBeInTheDocument();
    });

    // Mock 429 for POST message
    fetch.mockResolvedValueOnce({
      ok: false,
      status: 429,
      json: async () => ({ detail: 'Rate limit reached' })
    });

    fireEvent.change(screen.getByPlaceholderText(/type your message/i), { target: { value: 'test msg' } });
    fireEvent.click(screen.getByRole('button', { name: /send/i }));

    await waitFor(() => {
      expect(screen.getByText(/rate limit reached/i)).toBeInTheDocument();
    });
  });

  it('disables input while stream is active and appends streamed chunks', async () => {
    fetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ id: 'session-123', messages: [] })
    });

    render(<Chat />);
    fireEvent.click(screen.getByText('Select Session 123'));

    await waitFor(() => {
      expect(screen.getByPlaceholderText(/type your message/i)).toBeInTheDocument();
    });

    // Mock successful fetch stream
    // Simplified stream mock for the test
    const encoder = new TextEncoder();
    const mockStream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode('data: {"token": "Hel"}\n\n'));
        controller.enqueue(encoder.encode('data: {"token": "lo "}\n\n'));
        controller.enqueue(encoder.encode('data: {"token": "World"}\n\n'));
        controller.enqueue(encoder.encode('data: [DONE]\n\n'));
        controller.close();
      }
    });

    fetch.mockResolvedValueOnce({
      ok: true,
      body: mockStream,
    });

    const input = screen.getByPlaceholderText(/type your message/i);
    fireEvent.change(input, { target: { value: 'tell me a story' } });
    
    const sendButton = screen.getByRole('button', { name: /send/i });
    fireEvent.click(sendButton);

    expect(input).toBeDisabled();
    expect(sendButton).toBeDisabled();

    // Check if streamed content appears
    await waitFor(() => {
      expect(screen.getByText(/Hello World/i)).toBeInTheDocument();
    });
    
    // Input should be enabled again
    expect(input).not.toBeDisabled();
  });
});
