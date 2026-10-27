/**
 * The WEIGHTS group: the model as it is (Inspect) and as you might draw it
 * (Design). They were two modes of one pane, toggled by local state that was lost
 * on every remount; as rail sections they persist, and keep-alive means a design
 * with unsaved edits survives a look at the lens.
 */
import { ModelDesigner } from '../../interpretability/designer/ModelDesigner';
import { ModelInspector } from '../../interpretability/ModelExplorer';
import { SuiteFrame } from '../SuiteFrame';

export function ModelBody() {
  return (
    <SuiteFrame title="Model">
      <div className="mx-pane">
        <div className="mx-mode-body">
          <ModelInspector />
        </div>
      </div>
    </SuiteFrame>
  );
}

export function DesignBody() {
  return (
    <SuiteFrame title="Design">
      <div className="mx-pane">
        <div className="mx-mode-body">
          <ModelDesigner />
        </div>
      </div>
    </SuiteFrame>
  );
}
