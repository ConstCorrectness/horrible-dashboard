/**
 * The ACTIVATIONS group: a recorded forward pass, stepped like source, and read as
 * words. These were three sections of the llama.cpp pane (which keeps the server
 * and the model catalog) and the window's lens lab pane.
 */
import { useEffect, useState } from 'react';

import { usePaneSection } from '../../../layout/use-sections';
import { getLlamaModels, type ModelEntry } from '../../llamacpp/api';
import { LensSection } from '../../llamacpp/lens/LensSection';
import { LensCompare } from '../lenses/LensCompare';
import { StepperSection } from '../../llamacpp/stepper/StepperSection';
import { TracesSection } from '../../llamacpp/TracesSection';
import { LensLabPanel } from '../../webml/panels/LensLabPanel';
import { LENS_LAB_TABS } from '../../webml/lens-tabs';
import { SUITE_SECTIONS } from '../ids';
import { SubSections } from '../SubSections';
import { SuiteFrame } from '../SuiteFrame';

/**
 * The traces section picks a model to trace from the node's catalog, which the
 * llama.cpp pane used to hand it from its own poll. Here it is read once on mount:
 * the catalog changes when a download finishes, not every three seconds.
 */
export function TracesBody() {
  const [models, setModels] = useState<ModelEntry[]>([]);
  useEffect(() => {
    let alive = true;
    void getLlamaModels()
      .then((m) => alive && setModels(m.models))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);
  return (
    <SuiteFrame title="Traces">
      <div className="llama-pane">
        <TracesSection models={models} />
      </div>
    </SuiteFrame>
  );
}

export function StepperBody() {
  return (
    <SuiteFrame title="Stepper">
      <div className="llama-pane">
        <StepperSection />
      </div>
    </SuiteFrame>
  );
}

export const LENS_TABS = [
  { id: 'trace', label: 'Trace lens' },
  { id: 'lenses', label: 'Compare lenses' },
  ...LENS_LAB_TABS,
] as const;

/** Under the Lens section's scope, `usePaneSection()` names the lens tab. */
function LensSwitch() {
  const { section } = usePaneSection();
  if (section === 'trace') {
    return (
      <div className="llama-pane">
        <LensSection />
      </div>
    );
  }
  if (section === 'lenses') return <LensCompare />;
  return <LensLabPanel />;
}

export function LensBody() {
  return (
    <SuiteFrame title="Lens">
      <SubSections scope={SUITE_SECTIONS.lens} tabs={LENS_TABS}>
        <LensSwitch />
      </SubSections>
    </SuiteFrame>
  );
}
