import type { WebSocket } from 'ws';
export interface UpgradeWebSocketOptions {
    maxPayload?: number;
}
export declare function experimental_upgradeWebSocket(handler: (ws: WebSocket) => void | Promise<void>, options?: UpgradeWebSocketOptions): Promise<Response>;
