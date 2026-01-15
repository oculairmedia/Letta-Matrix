/**
 * Subscription Manager - Handle real-time message subscriptions
 */

import { randomUUID } from 'crypto';
import type { MatrixClient } from '@vector-im/matrix-bot-sdk';
import type { Storage } from './storage.js';
import type { MatrixClientPool } from './client-pool.js';
import type { MatrixEvent } from '../types/index.js';

export interface Subscription {
  id: string;
  identityId: string;
  rooms: string[];  // Empty = all rooms
  eventTypes: string[];
  createdAt: number;
  lastEventAt: number;
  eventCount: number;
}

export interface SubscriptionEvent {
  subscriptionId: string;
  event: MatrixEvent;
  room_id: string;
  timestamp: number;
}

type EventCallback = (event: SubscriptionEvent) => void;

export class SubscriptionManager {
  private storage: Storage;
  private clientPool: MatrixClientPool;
  private subscriptions: Map<string, Subscription> = new Map();
  private callbacks: Map<string, EventCallback[]> = new Map();
  private eventBuffer: Map<string, SubscriptionEvent[]> = new Map();
  private maxBufferSize = 100;

  constructor(storage: Storage, clientPool: MatrixClientPool) {
    this.storage = storage;
    this.clientPool = clientPool;
  }

  /**
   * Create a new subscription
   */
  async subscribe(
    identityId: string,
    rooms?: string[],
    eventTypes?: string[]
  ): Promise<Subscription> {
    const identity = await this.storage.getIdentityAsync(identityId);
    if (!identity) {
      throw new Error(`Identity not found: ${identityId}`);
    }

    const subscriptionId = randomUUID();
    
    // Get the client
    const client = await this.clientPool.getClientById(identityId);
    if (!client) {
      throw new Error(`Client not found for identity: ${identityId}`);
    }

    // If no rooms specified, get all joined rooms
    let targetRooms = rooms || [];
    if (targetRooms.length === 0) {
      targetRooms = await client.getJoinedRooms();
    }

    const subscription: Subscription = {
      id: subscriptionId,
      identityId,
      rooms: targetRooms,
      eventTypes: eventTypes || ['m.room.message'],
      createdAt: Date.now(),
      lastEventAt: Date.now(),
      eventCount: 0
    };

    this.subscriptions.set(subscriptionId, subscription);
    this.eventBuffer.set(subscriptionId, []);

    // Set up event listener on the client
    this.setupEventListener(client, subscription);

    console.log('[SubscriptionManager] Created subscription:', subscriptionId, 'for', identityId);
    return subscription;
  }

  /**
   * Set up event listener for a subscription
   */
  private setupEventListener(client: MatrixClient, subscription: Subscription): void {
    client.on('room.message', (roomId: string, event: any) => {
      // Check if this room is in the subscription
      if (subscription.rooms.length > 0 && !subscription.rooms.includes(roomId)) {
        return;
      }

      // Check event type
      const eventType = event.type || 'm.room.message';
      if (!subscription.eventTypes.includes(eventType)) {
        return;
      }

      const subEvent: SubscriptionEvent = {
        subscriptionId: subscription.id,
        event: {
          event_id: event.event_id,
          type: event.type,
          sender: event.sender,
          content: event.content,
          origin_server_ts: event.origin_server_ts,
          room_id: roomId
        },
        room_id: roomId,
        timestamp: Date.now()
      };

      // Update subscription stats
      subscription.lastEventAt = Date.now();
      subscription.eventCount++;

      // Add to buffer
      const buffer = this.eventBuffer.get(subscription.id) || [];
      buffer.push(subEvent);
      if (buffer.length > this.maxBufferSize) {
        buffer.shift();  // Remove oldest
      }
      this.eventBuffer.set(subscription.id, buffer);

      // Call registered callbacks
      const callbacks = this.callbacks.get(subscription.id) || [];
      callbacks.forEach(cb => {
        try {
          cb(subEvent);
        } catch (error) {
          console.error('[SubscriptionManager] Callback error:', error);
        }
      });
    });
  }

  /**
   * Unsubscribe
   */
  unsubscribe(subscriptionId: string): boolean {
    const deleted = this.subscriptions.delete(subscriptionId);
    this.eventBuffer.delete(subscriptionId);
    this.callbacks.delete(subscriptionId);
    
    if (deleted) {
      console.log('[SubscriptionManager] Removed subscription:', subscriptionId);
    }
    return deleted;
  }

  /**
   * Get subscription by ID
   */
  getSubscription(subscriptionId: string): Subscription | undefined {
    return this.subscriptions.get(subscriptionId);
  }

  /**
   * List all subscriptions for an identity
   */
  listSubscriptions(identityId?: string): Subscription[] {
    const all = Array.from(this.subscriptions.values());
    if (identityId) {
      return all.filter(s => s.identityId === identityId);
    }
    return all;
  }

  /**
   * Get buffered events for a subscription
   */
  getBufferedEvents(subscriptionId: string, since?: number): SubscriptionEvent[] {
    const buffer = this.eventBuffer.get(subscriptionId) || [];
    if (since) {
      return buffer.filter(e => e.timestamp > since);
    }
    return [...buffer];
  }

  /**
   * Register a callback for subscription events
   */
  onEvent(subscriptionId: string, callback: EventCallback): void {
    const callbacks = this.callbacks.get(subscriptionId) || [];
    callbacks.push(callback);
    this.callbacks.set(subscriptionId, callbacks);
  }

  /**
   * Clear all subscriptions
   */
  clearAll(): void {
    this.subscriptions.clear();
    this.eventBuffer.clear();
    this.callbacks.clear();
    console.log('[SubscriptionManager] Cleared all subscriptions');
  }
}
