import React, { useCallback } from 'react';
import { useEditor, EditorContent } from '@tiptap/react';
import { BubbleMenu, FloatingMenu } from '@tiptap/react/menus';
import StarterKit from '@tiptap/starter-kit';
import Placeholder from '@tiptap/extension-placeholder';
import { askAgent } from '../agent/orchestrator-client';

export const WriterEditor: React.FC = () => {
  const editor = useEditor({
    extensions: [
      StarterKit,
      Placeholder.configure({
        placeholder: 'Type "/" for commands or start writing...',
      }),
    ],
    content: '',
  });

  const [isAgentThinking, setIsAgentThinking] = React.useState(false);

  const handleAgentRewrite = useCallback(async () => {
    if (!editor || isAgentThinking) return;
    const { from, to } = editor.state.selection;
    const text = editor.state.doc.textBetween(from, to, ' ');
    if (!text) return;

    setIsAgentThinking(true);
    try {
      let result = '';
      await askAgent(
        `Please rewrite the following text to be more professional:\n\n${text}`,
        {
          onToken: (delta) => {
            result += delta;
            // Optionally could stream this live, but rewriting the block at the end is cleaner
          },
          onAnswer: (answer) => {
            editor.chain().focus().insertContentAt({ from, to }, answer || result).run();
          },
        },
        [],
        { agentId: 'writer' }
      );
    } finally {
      setIsAgentThinking(false);
    }
  }, [editor, isAgentThinking]);

  const handleAgentDraft = useCallback(async () => {
    if (!editor || isAgentThinking) return;
    
    setIsAgentThinking(true);
    try {
      let result = '';
      await askAgent(
        'Draft a continuation or new paragraph based on the current context.',
        {
          onToken: (delta) => {
            result += delta;
          },
          onAnswer: (answer) => {
            editor.chain().focus().insertContent(answer || result).run();
          },
        },
        [],
        { agentId: 'writer' }
      );
    } finally {
      setIsAgentThinking(false);
    }
  }, [editor, isAgentThinking]);

  if (!editor) {
    return null;
  }

  return (
    <div style={{ padding: '20px', height: '100%', display: 'flex', flexDirection: 'column' }}>
      <h1 style={{ marginBottom: '16px', color: 'var(--text-color, #e0e0e0)' }}>Writer Workspace</h1>
      
      <div style={{ flex: 1, border: '1px solid #333', borderRadius: '6px', backgroundColor: '#1a1a1a', padding: '10px', overflowY: 'auto' }}>
        
        {/* Bubble Menu: Appears when text is selected */}
        {editor && (
          <BubbleMenu editor={editor}>
            <div style={{ background: '#2c2c2c', padding: '4px', borderRadius: '4px', display: 'flex', gap: '4px', boxShadow: '0 2px 4px rgba(0,0,0,0.5)' }}>
              <button 
                onClick={() => editor.chain().focus().toggleBold().run()} 
                style={{ background: editor.isActive('bold') ? '#555' : 'transparent', color: '#fff', border: 'none', cursor: 'pointer', padding: '4px 8px', borderRadius: '4px' }}
              >
                Bold
              </button>
              <button 
                onClick={() => void handleAgentRewrite()}
                disabled={isAgentThinking}
                style={{ background: 'transparent', color: isAgentThinking ? '#aaa' : '#61dafb', border: 'none', cursor: isAgentThinking ? 'wait' : 'pointer', padding: '4px 8px', borderRadius: '4px' }}
              >
                {isAgentThinking ? '✨ Thinking...' : '✨ Rewrite'}
              </button>
            </div>
          </BubbleMenu>
        )}

        {/* Floating Menu: Appears on empty lines */}
        {editor && (
          <FloatingMenu editor={editor}>
            <div style={{ background: '#2c2c2c', padding: '4px', borderRadius: '4px', display: 'flex', gap: '4px', flexDirection: 'column', boxShadow: '0 2px 4px rgba(0,0,0,0.5)' }}>
              <button 
                onClick={() => editor.chain().focus().toggleHeading({ level: 2 }).run()}
                style={{ background: 'transparent', color: '#fff', border: 'none', cursor: 'pointer', padding: '4px 8px', textAlign: 'left' }}
              >
                H2 Heading
              </button>
              <button 
                onClick={() => void handleAgentDraft()}
                disabled={isAgentThinking}
                style={{ background: 'transparent', color: isAgentThinking ? '#aaa' : '#61dafb', border: 'none', cursor: isAgentThinking ? 'wait' : 'pointer', padding: '4px 8px', textAlign: 'left' }}
              >
                {isAgentThinking ? '✨ Generating...' : '✨ Agent Draft'}
              </button>
            </div>
          </FloatingMenu>
        )}

        {/* TipTap Editor Content */}
        <div style={{ color: '#ccc', minHeight: '100%', outline: 'none' }}>
          <EditorContent editor={editor} />
        </div>
      </div>
    </div>
  );
};
