import type { IncomingMessage } from 'http';
import type { Socket } from 'net';
import { RuntimeCache } from './cache/types';
import { PurgeApi } from './purge/types';
import { AddCacheTagApi } from './addcachetag/types';
type Context = {
    waitUntil?: (promise: Promise<unknown>) => void;
    deadline?: string;
    cache?: RuntimeCache;
    purge?: PurgeApi;
    addCacheTag?: AddCacheTagApi;
    headers?: Record<string, string>;
    /**
     * Low-level WebSocket upgrade provided by the runtime bridge.
     * Writes a 101 response on the underlying socket, detaches it
     * from the ServerResponse, and returns the raw req/socket/head
     * tuple for use with libraries like `ws`.
     */
    upgradeWebSocket?: () => {
        req: IncomingMessage;
        socket: Socket;
        head: Buffer;
    };
};
export declare const SYMBOL_FOR_REQ_CONTEXT: unique symbol;
export declare function getContext(): Context;
export {};
