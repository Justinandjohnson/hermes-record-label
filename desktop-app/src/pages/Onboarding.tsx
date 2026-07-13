import { useState } from "react";

interface OnboardingData {
  name: string;
  genre: string;
  influences: string;
  dawFolder: string;
  phone: string;
  quietStart: string;
  quietEnd: string;
  quietDays: string[];
}

const DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"];

export default function Onboarding() {
  const [step, setStep] = useState(0);
  const [data, setData] = useState<OnboardingData>({
    name: "",
    genre: "",
    influences: "",
    dawFolder: "",
    phone: "",
    quietStart: "22:00",
    quietEnd: "09:00",
    quietDays: [],
  });

  const update = (patch: Partial<OnboardingData>) => setData({ ...data, ...patch });

  const steps = [
    // Step 1: Sign Your Deal
    <div key="deal" className="space-y-6">
      <div>
        <h2 className="text-3xl font-bold text-zinc-100 mb-2">Sign your deal</h2>
        <p className="text-zinc-500">Welcome to the roster. Let's set up your label.</p>
      </div>
      <div className="space-y-4">
        <div>
          <label className="block text-sm text-zinc-400 mb-1">Artist name</label>
          <input
            type="text" value={data.name}
            onChange={(e) => update({ name: e.target.value })}
            className="w-full bg-surface-2 border border-surface-3 rounded-lg px-4 py-3 text-zinc-100 focus:outline-none focus:border-label-500"
            placeholder="Your name"
          />
        </div>
        <div>
          <label className="block text-sm text-zinc-400 mb-1">Primary genre</label>
          <input
            type="text" value={data.genre}
            onChange={(e) => update({ genre: e.target.value })}
            className="w-full bg-surface-2 border border-surface-3 rounded-lg px-4 py-3 text-zinc-100 focus:outline-none focus:border-label-500"
            placeholder="e.g. electronic, hip-hop, indie rock"
          />
        </div>
        <div>
          <label className="block text-sm text-zinc-400 mb-1">Influences</label>
          <input
            type="text" value={data.influences}
            onChange={(e) => update({ influences: e.target.value })}
            className="w-full bg-surface-2 border border-surface-3 rounded-lg px-4 py-3 text-zinc-100 focus:outline-none focus:border-label-500"
            placeholder="e.g. Burial, Aphex Twin, Radiohead"
          />
        </div>
      </div>
    </div>,

    // Step 2: DAW Folder
    <div key="daw" className="space-y-6">
      <div>
        <h2 className="text-3xl font-bold text-zinc-100 mb-2">Point to your DAW</h2>
        <p className="text-zinc-500">Where do you bounce / export your tracks?</p>
      </div>
      <div>
        <label className="block text-sm text-zinc-400 mb-1">Export folder path</label>
        <input
          type="text" value={data.dawFolder}
          onChange={(e) => update({ dawFolder: e.target.value })}
          className="w-full bg-surface-2 border border-surface-3 rounded-lg px-4 py-3 text-zinc-100 focus:outline-none focus:border-label-500"
          placeholder="/Users/you/Music/Bounces"
        />
        <p className="text-xs text-zinc-600 mt-2">
          We'll watch this folder for new audio files and automatically send them to A&R.
        </p>
      </div>
    </div>,

    // Step 3: Phone
    <div key="phone" className="space-y-6">
      <div>
        <h2 className="text-3xl font-bold text-zinc-100 mb-2">Connect your phone</h2>
        <p className="text-zinc-500">Your team will text you feedback and updates via SMS.</p>
      </div>
      <div>
        <label className="block text-sm text-zinc-400 mb-1">Phone number</label>
        <input
          type="tel" value={data.phone}
          onChange={(e) => update({ phone: e.target.value })}
          className="w-full bg-surface-2 border border-surface-3 rounded-lg px-4 py-3 text-zinc-100 focus:outline-none focus:border-label-500"
          placeholder="+1 (555) 123-4567"
        />
      </div>
    </div>,

    // Step 4: Quiet Hours
    <div key="quiet" className="space-y-6">
      <div>
        <h2 className="text-3xl font-bold text-zinc-100 mb-2">Set quiet hours</h2>
        <p className="text-zinc-500">When should your team leave you alone?</p>
      </div>
      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="block text-sm text-zinc-400 mb-1">Start</label>
          <input
            type="time" value={data.quietStart}
            onChange={(e) => update({ quietStart: e.target.value })}
            className="w-full bg-surface-2 border border-surface-3 rounded-lg px-4 py-3 text-zinc-100 focus:outline-none focus:border-label-500"
          />
        </div>
        <div>
          <label className="block text-sm text-zinc-400 mb-1">End</label>
          <input
            type="time" value={data.quietEnd}
            onChange={(e) => update({ quietEnd: e.target.value })}
            className="w-full bg-surface-2 border border-surface-3 rounded-lg px-4 py-3 text-zinc-100 focus:outline-none focus:border-label-500"
          />
        </div>
      </div>
      <div>
        <label className="block text-sm text-zinc-400 mb-2">DND days</label>
        <div className="flex flex-wrap gap-2">
          {DAYS.map((day) => (
            <button
              key={day}
              onClick={() =>
                update({
                  quietDays: data.quietDays.includes(day)
                    ? data.quietDays.filter((d) => d !== day)
                    : [...data.quietDays, day],
                })
              }
              className={`text-sm px-3 py-1.5 rounded-lg capitalize transition-colors ${
                data.quietDays.includes(day)
                  ? "bg-label-500 text-black"
                  : "bg-surface-2 text-zinc-400 hover:text-zinc-200"
              }`}
            >
              {day.slice(0, 3)}
            </button>
          ))}
        </div>
      </div>
    </div>,

    // Step 5: Meet Your Team
    <div key="team" className="space-y-6 text-center">
      <h2 className="text-3xl font-bold text-zinc-100 mb-2">Meet your team</h2>
      <p className="text-zinc-500 mb-8">
        Your label staff is ready. They'll introduce themselves via SMS.
      </p>
      <div className="grid grid-cols-2 gap-4 max-w-md mx-auto">
        {[
          { name: "Ravi", role: "A&R", color: "border-emerald-500" },
          { name: "Dez", role: "Manager", color: "border-blue-500" },
          { name: "Maren", role: "Creative Dir", color: "border-purple-500" },
          { name: "Sable", role: "Bandcamp", color: "border-orange-500" },
        ].map((agent) => (
          <div key={agent.name} className={`card border-l-4 ${agent.color}`}>
            <p className="font-semibold text-zinc-200">{agent.name}</p>
            <p className="text-xs text-zinc-500">{agent.role}</p>
          </div>
        ))}
      </div>
    </div>,
  ];

  const isLast = step === steps.length - 1;
  const canProceed =
    step === 0 ? data.name.trim().length > 0 :
    step === 1 ? data.dawFolder.trim().length > 0 :
    step === 2 ? data.phone.trim().length > 0 :
    true;

  return (
    <div className="min-h-screen flex flex-col items-center justify-center p-8">
      <div className="w-full max-w-lg">
        <div className="flex gap-1 mb-8">
          {steps.map((_, i) => (
            <div
              key={i}
              className={`h-1 flex-1 rounded-full ${i <= step ? "bg-label-500" : "bg-surface-3"}`}
            />
          ))}
        </div>

        {steps[step]}

        <div className="flex justify-between mt-8">
          <button
            onClick={() => setStep(Math.max(0, step - 1))}
            className={`btn-ghost text-sm ${step === 0 ? "invisible" : ""}`}
          >
            Back
          </button>
          <button
            onClick={() => {
              if (isLast) {
                // In production: save to DB via Tauri, trigger agent intros
                window.location.href = "/";
              } else {
                setStep(step + 1);
              }
            }}
            disabled={!canProceed}
            className="btn-primary text-sm disabled:opacity-40"
          >
            {isLast ? "Let's go" : "Continue"}
          </button>
        </div>
      </div>
    </div>
  );
}
