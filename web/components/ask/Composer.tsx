"use client";

import { useEffect, useRef, useState } from "react";
import { Paperclip, Mic, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ATTACHABLE_EXTENSIONS } from "@/lib/api";

// Web Speech API has no shared TS lib type; keep this narrow and local.
interface SpeechRecognitionLike extends EventTarget {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  start(): void;
  stop(): void;
  onresult: ((event: { results: { transcript: string }[][] } & Event) => void) | null;
  onend: (() => void) | null;
}

export function Composer({
  onSubmit,
  disabled,
}: {
  onSubmit: (text: string, files: File[]) => void;
  disabled?: boolean;
}) {
  const [value, setValue] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [listening, setListening] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const [voiceSupported, setVoiceSupported] = useState(false);

  useEffect(() => {
    const Ctor =
      (window as unknown as { SpeechRecognition?: new () => SpeechRecognitionLike }).SpeechRecognition ??
      (window as unknown as { webkitSpeechRecognition?: new () => SpeechRecognitionLike }).webkitSpeechRecognition;
    if (!Ctor) return;
    setVoiceSupported(true);
    const recognition = new Ctor();
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.lang = "en-US";
    recognition.onresult = (event) => {
      const transcript = event.results[event.results.length - 1]?.[0]?.transcript ?? "";
      setValue((v) => (v ? `${v} ${transcript}` : transcript));
    };
    recognition.onend = () => setListening(false);
    recognitionRef.current = recognition;
  }, []);

  const toggleListening = () => {
    if (!recognitionRef.current) return;
    if (listening) {
      recognitionRef.current.stop();
      setListening(false);
    } else {
      recognitionRef.current.start();
      setListening(true);
    }
  };

  const submit = () => {
    const text = value.trim();
    if (!text || disabled) return;
    onSubmit(text, files);
    setValue("");
    setFiles([]);
  };

  return (
    <div className="flex-none border-t border-border px-5 py-3.5">
      {/* Matches the conversation column above it -- see ask/page.tsx. */}
      <div className="mx-auto w-full max-w-[760px] xl:max-w-[920px] 2xl:max-w-[1040px]">
        {files.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-1.5">
            {files.map((f, i) => (
              <span
                key={`${f.name}-${i}`}
                className="flex items-center gap-1.5 rounded-full border border-border bg-surface-2 px-2.5 py-1 text-[11.5px] text-text"
              >
                {f.name}
                <button onClick={() => setFiles((fs) => fs.filter((_, j) => j !== i))} aria-label={`Remove ${f.name}`}>
                  <X className="h-3 w-3 text-text-faint" />
                </button>
              </span>
            ))}
          </div>
        )}
        <div className="flex gap-2">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept={ATTACHABLE_EXTENSIONS.join(",")}
            className="hidden"
            onChange={(e) => {
              if (e.target.files) setFiles((fs) => [...fs, ...Array.from(e.target.files!)]);
              e.target.value = "";
            }}
          />
          <Button
            type="button"
            variant="outline"
            size="icon"
            className="flex-none rounded-full"
            title="Attach a file"
            onClick={() => fileInputRef.current?.click()}
            disabled={disabled}
          >
            <Paperclip className="h-4 w-4" />
          </Button>
          {voiceSupported && (
            <Button
              type="button"
              variant={listening ? "default" : "outline"}
              size="icon"
              className="flex-none rounded-full"
              title="Speak your question"
              onClick={toggleListening}
              disabled={disabled}
            >
              <Mic className="h-4 w-4" />
            </Button>
          )}
          <input
            className="flex-1 rounded-full border border-border-strong bg-background px-4 py-2.5 text-[13px] text-text placeholder:text-text-faint focus-visible:border-brand"
            placeholder={listening ? "Listening…" : "Ask me anything…"}
            value={value}
            disabled={disabled}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") submit();
            }}
          />
          <Button className="flex-none rounded-full px-5" onClick={submit} disabled={disabled || !value.trim()}>
            Ask
          </Button>
        </div>
      </div>
    </div>
  );
}
