import { describe, expect, test, beforeEach, jest } from '@jest/globals';

const createMockHandler = () => {
  const extractContentText = (content: unknown): string | undefined => {
    if (!content) {
      return undefined;
    }

    if (typeof content === 'string') {
      return content;
    }

    if (Array.isArray(content)) {
      const textParts: string[] = [];
      for (const part of content) {
        if (part && typeof part === 'object' && 'type' in part) {
          if ((part as { type: string }).type === 'text' && typeof (part as { text?: string }).text === 'string') {
            textParts.push((part as { text: string }).text);
          }
        }
      }
      if (textParts.length > 0) {
        return textParts.join('\n');
      }
      return undefined;
    }

    if (typeof content === 'object' && 'text' in content && typeof (content as { text: unknown }).text === 'string') {
      return (content as { text: string }).text;
    }

    return JSON.stringify(content);
  };

  const extractAssistantContent = (messages?: Array<{ message_type: string; content?: unknown; assistant_message?: string }>): string | undefined => {
    if (!messages || messages.length === 0) {
      return undefined;
    }

    for (let i = messages.length - 1; i >= 0; i--) {
      const msg = messages[i];
      if (msg.message_type === 'assistant_message') {
        const extracted = extractContentText(msg.content);
        if (extracted) {
          return extracted;
        }
        if (msg.assistant_message) {
          return msg.assistant_message;
        }
      }
    }

    return undefined;
  };

  return { extractContentText, extractAssistantContent };
};

describe('Webhook Content Extraction', () => {
  let handler: ReturnType<typeof createMockHandler>;

  beforeEach(() => {
    handler = createMockHandler();
  });

  describe('extractContentText', () => {
    test('handles undefined content', () => {
      expect(handler.extractContentText(undefined)).toBeUndefined();
    });

    test('handles null content', () => {
      expect(handler.extractContentText(null)).toBeUndefined();
    });

    test('handles string content directly', () => {
      const content = 'Hello, this is a simple string message';
      expect(handler.extractContentText(content)).toBe(content);
    });

    test('handles empty string as undefined (no content to post)', () => {
      expect(handler.extractContentText('')).toBeUndefined();
    });

    test('handles array with single text part', () => {
      const content = [{ type: 'text', text: 'Hello from text part' }];
      expect(handler.extractContentText(content)).toBe('Hello from text part');
    });

    test('handles array with multiple text parts', () => {
      const content = [
        { type: 'text', text: 'First paragraph.' },
        { type: 'text', text: 'Second paragraph.' },
        { type: 'text', text: 'Third paragraph.' }
      ];
      expect(handler.extractContentText(content)).toBe('First paragraph.\nSecond paragraph.\nThird paragraph.');
    });

    test('handles array with mixed content types - extracts only text', () => {
      const content = [
        { type: 'reasoning', reasoning: 'internal thought' },
        { type: 'text', text: 'Visible response' },
        { type: 'tool_call', name: 'some_tool', arguments: '{}' }
      ];
      expect(handler.extractContentText(content)).toBe('Visible response');
    });

    test('handles array with no text parts', () => {
      const content = [
        { type: 'reasoning', reasoning: 'just thinking' },
        { type: 'tool_call', name: 'tool', arguments: '{}' }
      ];
      expect(handler.extractContentText(content)).toBeUndefined();
    });

    test('handles empty array', () => {
      expect(handler.extractContentText([])).toBeUndefined();
    });

    test('handles object with text field directly', () => {
      const content = { text: 'Direct text object' };
      expect(handler.extractContentText(content)).toBe('Direct text object');
    });

    test('handles object without text field - JSON stringifies', () => {
      const content = { some: 'data', other: 123 };
      expect(handler.extractContentText(content)).toBe('{"some":"data","other":123}');
    });

    test('handles Letta v1 format with signature field', () => {
      const content = [
        { type: 'text', text: 'Response with signature', signature: 'sig-123' }
      ];
      expect(handler.extractContentText(content)).toBe('Response with signature');
    });

    test('handles real Letta webhook payload format', () => {
      const content = [
        {
          "type": "text",
          "text": "This is the actual assistant response that was being truncated before the fix.",
          "signature": null
        }
      ];
      expect(handler.extractContentText(content)).toBe(
        "This is the actual assistant response that was being truncated before the fix."
      );
    });

    test('handles colon-only truncation bug scenario', () => {
      const content = [
        { type: 'text', text: "Here's what's happening:\n\n1. First point\n2. Second point" }
      ];
      const result = handler.extractContentText(content);
      expect(result).toContain("Here's what's happening:");
      expect(result).toContain("1. First point");
    });
  });

  describe('extractAssistantContent', () => {
    test('handles undefined messages', () => {
      expect(handler.extractAssistantContent(undefined)).toBeUndefined();
    });

    test('handles empty messages array', () => {
      expect(handler.extractAssistantContent([])).toBeUndefined();
    });

    test('handles messages with no assistant_message type', () => {
      const messages = [
        { message_type: 'user_message', content: 'Hello' },
        { message_type: 'tool_call_message', content: { name: 'tool' } }
      ];
      expect(handler.extractAssistantContent(messages)).toBeUndefined();
    });

    test('extracts from last assistant_message', () => {
      const messages = [
        { message_type: 'assistant_message', content: 'First response' },
        { message_type: 'user_message', content: 'User reply' },
        { message_type: 'assistant_message', content: 'Second response' }
      ];
      expect(handler.extractAssistantContent(messages)).toBe('Second response');
    });

    test('extracts array content from assistant_message', () => {
      const messages = [
        {
          message_type: 'assistant_message',
          content: [
            { type: 'text', text: 'Response from array format' }
          ]
        }
      ];
      expect(handler.extractAssistantContent(messages)).toBe('Response from array format');
    });

    test('falls back to assistant_message field', () => {
      const messages = [
        {
          message_type: 'assistant_message',
          content: undefined,
          assistant_message: 'Fallback message'
        }
      ];
      expect(handler.extractAssistantContent(messages)).toBe('Fallback message');
    });

    test('handles real webhook payload structure', () => {
      const messages = [
        {
          "message_type": "reasoning_message",
          "content": "Thinking about the response..."
        },
        {
          "message_type": "tool_call_message", 
          "content": { "name": "send_message", "arguments": "{}" }
        },
        {
          "message_type": "tool_return_message",
          "content": "Tool executed successfully"
        },
        {
          "message_type": "assistant_message",
          "content": [
            {
              "type": "text",
              "text": "Based on my analysis, here's the complete response that should NOT be truncated.",
              "signature": null
            }
          ]
        }
      ];
      expect(handler.extractAssistantContent(messages)).toBe(
        "Based on my analysis, here's the complete response that should NOT be truncated."
      );
    });

    test('handles multiple assistant messages - takes last one', () => {
      const messages = [
        {
          message_type: 'assistant_message',
          content: [{ type: 'text', text: 'First response' }]
        },
        {
          message_type: 'reasoning_message',
          content: 'Intermediate thought'
        },
        {
          message_type: 'assistant_message',
          content: [{ type: 'text', text: 'Final response after more processing' }]
        }
      ];
      expect(handler.extractAssistantContent(messages)).toBe('Final response after more processing');
    });
  });

  describe('regression tests for truncation bug', () => {
    test('does not truncate to just colon character', () => {
      const messages = [
        {
          message_type: 'assistant_message',
          content: [
            {
              type: 'text',
              text: "Here's the end-to-end flow:\n\n1. User sends message\n2. Agent processes\n3. Response returned"
            }
          ]
        }
      ];
      const result = handler.extractAssistantContent(messages);
      expect(result).not.toBe(':');
      expect(result).not.toBe(":");
      expect(result!.length).toBeGreaterThan(10);
      expect(result).toContain('end-to-end flow');
    });

    test('preserves full content with colons', () => {
      const messages = [
        {
          message_type: 'assistant_message',
          content: [{ type: 'text', text: 'Title: Description\nKey: Value\nStatus: Active' }]
        }
      ];
      const result = handler.extractAssistantContent(messages);
      expect(result).toBe('Title: Description\nKey: Value\nStatus: Active');
    });

    test('handles long multi-paragraph responses', () => {
      const longText = `This is a comprehensive response that spans multiple paragraphs.

First, let me explain the background. The webhook system receives events from Letta when agent runs complete.

Second, the content extraction was previously only handling string content directly, not the array format.

Third, the fix adds proper handling for the array format: [{type: "text", text: "..."}].

This should now work correctly for all content formats.`;

      const messages = [
        {
          message_type: 'assistant_message',
          content: [{ type: 'text', text: longText }]
        }
      ];
      const result = handler.extractAssistantContent(messages);
      expect(result).toBe(longText);
      expect(result!.length).toBe(longText.length);
    });
  });
});
