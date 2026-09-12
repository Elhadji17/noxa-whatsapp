/**
 * Reports a custom metric for the current Vercel Function invocation.
 *
 * @param name The name of the metric.
 * @param value The numeric value of the metric.
 * @param tags Optional tags to attach to the metric.
 *
 * @example
 *
 * ```js
 * import { metric } from '@vercel/functions';
 *
 * metric('tinybird.query_ms', 100, {
 *   query: 'getUser',
 * });
 * ```
 */
export declare function metric(name: string, value: number, tags?: Record<string, string>): void;
