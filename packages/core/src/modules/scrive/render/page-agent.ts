/**
 * What a page pane lets its blocks ask of the agent: send a prompt about this page,
 * after saving it so the agent reads what is on screen. `null` everywhere else (a
 * notebook's stored outputs, a test render), where blocks offer no agent actions.
 */
import { createContext } from 'react';

export const PageAgentContext = createContext<((prompt: string) => void) | null>(null);
