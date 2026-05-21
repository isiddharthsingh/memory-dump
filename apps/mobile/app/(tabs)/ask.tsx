import { Ionicons } from "@expo/vector-icons";
import { useHeaderHeight } from "@react-navigation/elements";
import * as Haptics from "expo-haptics";
import { Link } from "expo-router";
import {
  ExpoSpeechRecognitionModule,
  useSpeechRecognitionEvent,
} from "expo-speech-recognition";
import { useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import { api, type Citation } from "../../lib/api";
import { TOUCH_TARGET, cardShadow, radii, theme } from "../../lib/theme";

export default function AskScreen() {
  const headerHeight = useHeaderHeight();
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<string | null>(null);
  const [citations, setCitations] = useState<Citation[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [listening, setListening] = useState(false);
  const streamRef = useRef<{ abort: () => void } | null>(null);

  useEffect(() => {
    return () => {
      streamRef.current?.abort();
      ExpoSpeechRecognitionModule.stop();
    };
  }, []);

  useSpeechRecognitionEvent("result", (e) => {
    const t = e.results?.[0]?.transcript;
    if (t) setQuestion(t);
  });
  useSpeechRecognitionEvent("end", () => setListening(false));
  useSpeechRecognitionEvent("error", () => setListening(false));

  const toggleVoice = async () => {
    if (listening) {
      ExpoSpeechRecognitionModule.stop();
      setListening(false);
      return;
    }
    const perm = await ExpoSpeechRecognitionModule.requestPermissionsAsync();
    if (!perm.granted) return;
    void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
    setListening(true);
    ExpoSpeechRecognitionModule.start({
      lang: "en-US",
      interimResults: true,
      continuous: false,
    });
  };

  const submit = (q: string) => {
    if (!q || streaming) return;
    streamRef.current?.abort();
    setAnswer("");
    setCitations([]);
    setStreaming(true);
    streamRef.current = api.askStream(q, {
      onToken: (t) => setAnswer((prev) => (prev ?? "") + t),
      onDone: (c) => {
        setCitations(c);
        setStreaming(false);
      },
      onError: (err) => {
        setAnswer(`Error: ${err.message}`);
        setCitations([]);
        setStreaming(false);
      },
    });
  };

  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === "ios" ? "padding" : "height"}
      keyboardVerticalOffset={Platform.OS === "ios" ? headerHeight : 0}
    >
      <ScrollView
        contentContainerStyle={styles.scroll}
        keyboardShouldPersistTaps="handled"
      >
        {answer !== null ? (
          <View style={styles.answerBox}>
            <Text style={styles.answerText}>
              {renderAnswer(answer)}
              {streaming ? <Text style={styles.caret}>▍</Text> : null}
            </Text>
            {citations.length > 0 ? (
              <View style={styles.citations}>
                <Text style={styles.sectionLabel}>Sources</Text>
                {citations.map((c) => (
                  <CitationRow key={`${c.kind}:${c.id}`} citation={c} />
                ))}
              </View>
            ) : null}
          </View>
        ) : (
          <View style={styles.intro}>
            <Text style={styles.introTitle}>Ask your memory</Text>
            <Text style={styles.introText}>
              Try: "that Karpathy video on async runtimes" or "have I saved
              anything about rust async?"
            </Text>
          </View>
        )}
      </ScrollView>

      <View style={styles.inputBar}>
        <Pressable
          onPress={toggleVoice}
          style={({ pressed }) => [
            styles.micBtn,
            listening && styles.micBtnActive,
            pressed && styles.micBtnPressed,
          ]}
          disabled={streaming}
          accessibilityRole="button"
          accessibilityLabel={listening ? "Stop listening" : "Start voice input"}
          accessibilityState={{ disabled: streaming, selected: listening }}
        >
          <Ionicons
            name={listening ? "stop" : "mic"}
            size={20}
            color={listening ? "#FFFFFF" : theme.accent}
          />
        </Pressable>
        <TextInput
          style={styles.input}
          placeholder="Ask anything…"
          placeholderTextColor={theme.textFaint}
          value={question}
          onChangeText={setQuestion}
          onSubmitEditing={() => submit(question.trim())}
          returnKeyType="send"
          editable={!streaming}
        />
        <Pressable
          onPress={() => submit(question.trim())}
          style={({ pressed }) => [
            styles.sendBtn,
            (streaming || !question.trim()) && { opacity: 0.5 },
            pressed && styles.sendBtnPressed,
          ]}
          disabled={streaming || !question.trim()}
          accessibilityRole="button"
          accessibilityLabel="Send question"
          accessibilityState={{ disabled: streaming || !question.trim() }}
        >
          {streaming ? (
            <ActivityIndicator color="#FFFFFF" />
          ) : (
            <Text style={styles.sendText}>Ask</Text>
          )}
        </Pressable>
      </View>
    </KeyboardAvoidingView>
  );
}

function CitationRow({ citation }: { citation: Citation }) {
  if (citation.kind === "item") {
    return (
      <Link href={{ pathname: "/item/[id]", params: { id: citation.id } }} asChild>
        <Pressable style={styles.citationRow}>
          <Text style={styles.citationKind}>item</Text>
          <Text style={styles.citationTitle} numberOfLines={2}>
            {citation.title ?? citation.url ?? "(untitled)"}
          </Text>
        </Pressable>
      </Link>
    );
  }
  return (
    <Link href={{ pathname: "/wiki/[slug]", params: { slug: citation.slug ?? "" } }} asChild>
      <Pressable style={styles.citationRow}>
        <Text style={styles.citationKind}>wiki</Text>
        <Text style={styles.citationTitle} numberOfLines={2}>
          {citation.title ?? citation.slug ?? "(untitled)"}
        </Text>
      </Pressable>
    </Link>
  );
}

/** Strip [item:UUID] and [wiki:slug] markers from the answer text. */
function renderAnswer(answer: string): string {
  return answer.replace(/\[(item|wiki):[^\]]+\]/g, "").replace(/  +/g, " ");
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: theme.bg },
  scroll: { padding: 16, flexGrow: 1 },
  intro: { flex: 1, justifyContent: "center", gap: 8 },
  introTitle: {
    color: theme.text,
    fontSize: 24,
    fontWeight: "700",
    letterSpacing: -0.3,
  },
  introText: { color: theme.textMuted, fontSize: 15, lineHeight: 21 },
  answerBox: {
    backgroundColor: theme.surface,
    borderRadius: radii.lg,
    padding: 18,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: theme.border,
    ...cardShadow("sm"),
  },
  answerText: { color: theme.text, fontSize: 16, lineHeight: 23 },
  caret: { color: theme.accent, fontSize: 16 },
  citations: { marginTop: 16, gap: 6 },
  sectionLabel: {
    color: theme.textMuted,
    fontSize: 11,
    fontWeight: "700",
    textTransform: "uppercase",
    letterSpacing: 0.5,
    marginBottom: 4,
  },
  citationRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingVertical: 8,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: theme.border,
  },
  citationKind: {
    color: theme.accent,
    fontSize: 11,
    fontWeight: "700",
    width: 44,
    textTransform: "uppercase",
    letterSpacing: 0.3,
  },
  citationTitle: { color: theme.text, fontSize: 14, flex: 1 },
  inputBar: {
    flexDirection: "row",
    padding: 12,
    gap: 8,
    alignItems: "center",
    backgroundColor: theme.surface,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: theme.border,
  },
  input: {
    flex: 1,
    backgroundColor: theme.surfaceSunken,
    color: theme.text,
    borderRadius: radii.pill,
    paddingHorizontal: 16,
    paddingVertical: 10,
    fontSize: 15,
    minHeight: TOUCH_TARGET,
  },
  sendBtn: {
    backgroundColor: theme.accent,
    borderRadius: radii.pill,
    paddingHorizontal: 20,
    justifyContent: "center",
    minWidth: 72,
    minHeight: TOUCH_TARGET,
    alignItems: "center",
    ...cardShadow("sm"),
  },
  sendBtnPressed: { backgroundColor: theme.accentPressed },
  sendText: { color: "#FFFFFF", fontWeight: "700", letterSpacing: -0.2 },
  micBtn: {
    width: TOUCH_TARGET,
    height: TOUCH_TARGET,
    borderRadius: radii.pill,
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 1,
    borderColor: theme.accent,
    backgroundColor: theme.surface,
  },
  micBtnActive: { backgroundColor: theme.accent, borderColor: theme.accent },
  micBtnPressed: { opacity: 0.7 },
});
