import { DangerouslyDeleteOptions } from './types';
export declare const invalidateByTag: (tag: string | string[]) => Promise<void>;
export declare const dangerouslyDeleteByTag: (tag: string | string[], options?: DangerouslyDeleteOptions) => Promise<void>;
export declare const invalidateBySrcImage: (src: string | string[]) => Promise<void>;
export declare const dangerouslyDeleteBySrcImage: (src: string | string[], options?: DangerouslyDeleteOptions) => Promise<void>;
