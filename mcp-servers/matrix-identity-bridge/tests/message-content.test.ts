/**
 * Tests for message content building, including rich replies
 */
import { describe, it, expect } from '@jest/globals';

describe('Message Content Building', () => {
  /**
   * Helper function that mirrors the logic in MatrixMessaging.ts for building message content
   */
  function buildMessageContent(
    message: string,
    msgtype: string = 'm.text',
    replyToEventId?: string
  ): Record<string, unknown> {
    const content: Record<string, unknown> = {
      msgtype,
      body: message,
    };

    if (replyToEventId) {
      content['m.relates_to'] = {
        'm.in_reply_to': {
          event_id: replyToEventId,
        },
      };
    }

    return content;
  }

  describe('Basic Messages', () => {
    it('should build a simple text message', () => {
      const content = buildMessageContent('Hello, world!');

      expect(content.msgtype).toBe('m.text');
      expect(content.body).toBe('Hello, world!');
      expect(content['m.relates_to']).toBeUndefined();
    });

    it('should build a notice message', () => {
      const content = buildMessageContent('This is a notice', 'm.notice');

      expect(content.msgtype).toBe('m.notice');
      expect(content.body).toBe('This is a notice');
    });

    it('should handle empty message body', () => {
      const content = buildMessageContent('');

      expect(content.body).toBe('');
      expect(content.msgtype).toBe('m.text');
    });

    it('should handle multiline messages', () => {
      const multiline = 'Line 1\nLine 2\nLine 3';
      const content = buildMessageContent(multiline);

      expect(content.body).toBe(multiline);
    });

    it('should handle special characters', () => {
      const special = 'Test with émojis 🎉 and spëcial châràctérs!';
      const content = buildMessageContent(special);

      expect(content.body).toBe(special);
    });
  });

  describe('Rich Replies', () => {
    it('should add m.relates_to for replies', () => {
      const content = buildMessageContent(
        'This is a reply',
        'm.text',
        '$eventId123'
      );

      expect(content['m.relates_to']).toBeDefined();
      const relatesTo = content['m.relates_to'] as Record<string, unknown>;
      expect(relatesTo['m.in_reply_to']).toBeDefined();

      const inReplyTo = relatesTo['m.in_reply_to'] as Record<string, unknown>;
      expect(inReplyTo.event_id).toBe('$eventId123');
    });

    it('should preserve message body in replies', () => {
      const content = buildMessageContent(
        'Reply message body',
        'm.text',
        '$eventId456'
      );

      expect(content.body).toBe('Reply message body');
      expect(content.msgtype).toBe('m.text');
    });

    it('should handle event IDs with special characters', () => {
      // Matrix event IDs can contain various characters
      const eventId = '$WNZtQmqaHwA9u-gZJsBPsoGe0ak_PhPRWuG2E8ZNBqo';
      const content = buildMessageContent('Reply', 'm.text', eventId);

      const relatesTo = content['m.relates_to'] as Record<string, unknown>;
      const inReplyTo = relatesTo['m.in_reply_to'] as Record<string, unknown>;
      expect(inReplyTo.event_id).toBe(eventId);
    });

    it('should not add m.relates_to when replyToEventId is undefined', () => {
      const content = buildMessageContent('No reply', 'm.text', undefined);

      expect(content['m.relates_to']).toBeUndefined();
    });

    it('should not add m.relates_to when replyToEventId is empty string', () => {
      // Empty string should be treated as no reply (falsy)
      const content = buildMessageContent('No reply', 'm.text', '');

      // Empty string is falsy, so no relation should be added
      expect(content['m.relates_to']).toBeUndefined();
    });
  });

  describe('Matrix Event Content Structure', () => {
    it('should produce valid Matrix message event content', () => {
      const content = buildMessageContent('Test message');

      // Required fields for m.room.message
      expect(content).toHaveProperty('msgtype');
      expect(content).toHaveProperty('body');
      expect(typeof content.msgtype).toBe('string');
      expect(typeof content.body).toBe('string');
    });

    it('should produce valid Matrix reply event content structure', () => {
      const content = buildMessageContent('Test reply', 'm.text', '$event123');

      // Verify the reply structure
      expect(content['m.relates_to']).toBeDefined();
      
      const relatesTo = content['m.relates_to'] as Record<string, unknown>;
      expect(relatesTo['m.in_reply_to']).toBeDefined();

      const inReplyTo = relatesTo['m.in_reply_to'] as Record<string, unknown>;
      expect(inReplyTo.event_id).toBe('$event123');
      expect(typeof inReplyTo.event_id).toBe('string');
    });
  });
});
