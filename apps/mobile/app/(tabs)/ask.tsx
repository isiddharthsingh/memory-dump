import { Ionicons } from "@expo/vector-icons";
import { useQuery } from "@tanstack/react-query";
import * as Haptics from "expo-haptics";
import { useLocalSearchParams } from "expo-router";
import { ExpoSpeechRecognitionModule, useSpeechRecognitionEvent } from "expo-speech-recognition";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import { AskTurn, type Turn } from "../../components/AskTurn";
import { useKeyboardVisible, useTabBarSpace } from "../../components/FloatingTabBar";
import { Chip, IconButton, ScreenHeader } from "../../components/ui";
import { api } from "../../lib/api";
import { humanizeTag, topicTitle } from "../../lib/format";
import { TOUCH_TARGET, colors, fonts, radii, spacing, type } from "../../lib/theme";

const SUGGESTIONS = 3;

export default function AskScreen() {
  const params = useLocalSearchParams<{ q?: string }>();
  const tabBarSpace = useTabBarSpace();
  const keyboardVisible = useKeyboardVisible();
  const [input, setInput] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [listening, setListening] = useState(false);
  const streamRef = useRef<{ abort: () => void } | null>(null);
  const scrollRef = useRef<ScrollView>(null);
  const nextId = useRef(1);

  const tags = useQuery({ queryKey: ["tags"], queryFn: () => api.listTags() });
  const wiki = useQuery({ queryKey: ["wiki"], queryFn: () => api.listWiki() });
  const streaming = turns.some((t) => t.status === "streaming");

  // "Ask about this" on the reader opens this tab with a drafted question.
  const [draftedFrom, setDraftedFrom] = useState<string | undefined>(undefined);
  if (params.q !== draftedFrom) {
    setDraftedFrom(params.q);
    if (params.q) setInput(params.q);
  }

  useEffect(() => {
    return () => {
      streamRef.current?.abort();
      ExpoSpeechRecognitionModule.stop();
    };
  }, []);

  useSpeechRecognitionEvent("result", (e) => {
    const t = e.results?.[0]?.transcript;
    if (t) setInput(t);
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
    ExpoSpeechRecognitionModule.start({ lang: "en-US", interimResults: true, continuous: false });
  };

  const updateTurn = useCallback((id: number, patch: (t: Turn) => Partial<Turn>) => {
    setTurns((prev) => prev.map((t) => (t.id === id ? { ...t, ...patch(t) } : t)));
  }, []);

  const run = useCallback(
    (id: number, question: string) => {
      streamRef.current?.abort();
      streamRef.current = api.askStream(question, {
        onToken: (text) => updateTurn(id, (t) => ({ answer: t.answer + text })),
        onDone: (citations) => updateTurn(id, () => ({ citations, status: "done" })),
        onError: (err) => updateTurn(id, () => ({ status: "error", error: err.message })),
      });
    },
    [updateTurn],
  );

  const submit = (raw: string) => {
    const question = raw.trim();
    if (!question || streaming) return;
    const id = nextId.current++;
    setTurns((prev) => [...prev, { id, question, answer: "", citations: [], status: "streaming" }]);
    setInput("");
    run(id, question);
  };

  const retry = (turn: Turn) => {
    updateTurn(turn.id, () => ({ answer: "", citations: [], status: "streaming", error: undefined }));
    run(turn.id, turn.question);
  };

  const stop = () => {
    streamRef.current?.abort();
    setTurns((prev) => prev.map((t) => (t.status === "streaming" ? { ...t, status: "done" } : t)));
  };

  // Wiki titles are properly cased phrases ("Large Language Models"); tags
  // are slugs, so they're only a fallback for a library without a wiki yet.
  const topics = (wiki.data ?? []).map((p) => topicTitle(p.title));
  const subjects = topics.length > 0 ? topics : (tags.data ?? []).map((t) => humanizeTag(t.tag));
  const suggestions = [
    ...subjects.slice(0, SUGGESTIONS).map((s) => `What do I know about ${s}?`),
    "What did I save this week?",
  ];
  const canSend = input.trim().length > 0 && !streaming;

  return (
    <KeyboardAvoidingView style={styles.container} behavior={Platform.OS === "ios" ? "padding" : undefined}>
      <ScreenHeader
        title="Ask"
        right={
          turns.length > 0 ? (
            <IconButton
              icon="create-outline"
              accessibilityLabel="New question"
              onPress={() => {
                stop();
                setTurns([]);
              }}
            />
          ) : null
        }
      />
      <ScrollView
        ref={scrollRef}
        contentContainerStyle={styles.scroll}
        keyboardShouldPersistTaps="handled"
        keyboardDismissMode="interactive"
        onContentSizeChange={() => turns.length > 0 && scrollRef.current?.scrollToEnd({ animated: true })}
      >
        {turns.length === 0 ? (
          <View style={styles.intro}>
            <Text style={styles.introTitle}>Ask your library</Text>
            <Text style={styles.introText}>
              Answers come only from what you’ve saved, with numbered sources you can open.
            </Text>
            <View style={styles.suggestions}>
              {suggestions.map((s) => (
                <Chip key={s} label={s} onPress={() => submit(s)} />
              ))}
            </View>
          </View>
        ) : (
          turns.map((t) => <AskTurn key={t.id} turn={t} onRetry={() => retry(t)} />)
        )}
      </ScrollView>

      <View style={[styles.composerWrap, { paddingBottom: keyboardVisible ? spacing.sm : tabBarSpace }]}>
        <View style={styles.composer}>
          <IconButton
            icon={listening ? "stop-circle" : "mic-outline"}
            color={listening ? colors.failed : colors.ink2}
            accessibilityLabel={listening ? "Stop listening" : "Ask with your voice"}
            onPress={() => void toggleVoice()}
          />
          <TextInput
            style={styles.input}
            placeholder={turns.length > 0 ? "Ask another question" : "Ask anything you’ve saved"}
            placeholderTextColor={colors.ink3}
            value={input}
            onChangeText={setInput}
            multiline
            submitBehavior="submit"
            returnKeyType="send"
            onSubmitEditing={() => submit(input)}
            accessibilityLabel="Question"
          />
          <Pressable
            onPress={() => (streaming ? stop() : submit(input))}
            disabled={!streaming && !canSend}
            style={[styles.send, !streaming && !canSend && styles.sendDisabled]}
            accessibilityRole="button"
            accessibilityLabel={streaming ? "Stop answering" : "Send question"}
            accessibilityState={{ disabled: !streaming && !canSend }}
          >
            <Ionicons name={streaming ? "stop" : "arrow-up"} size={streaming ? 14 : 18} color={colors.onInk} />
          </Pressable>
        </View>
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  scroll: { paddingHorizontal: spacing.lg, paddingTop: spacing.sm, paddingBottom: spacing.xl, flexGrow: 1 },
  intro: { flex: 1, justifyContent: "flex-end", gap: spacing.sm, paddingBottom: spacing.md },
  introTitle: { fontFamily: fonts.serifSemibold, fontSize: 28, lineHeight: 34, color: colors.ink },
  introText: { ...type.callout, color: colors.ink2, marginBottom: spacing.sm },
  suggestions: { alignItems: "flex-start" },
  composerWrap: { paddingHorizontal: spacing.lg, paddingTop: spacing.sm, backgroundColor: colors.bg },
  composer: {
    flexDirection: "row",
    alignItems: "flex-end",
    gap: spacing.xs,
    minHeight: 54,
    paddingVertical: 5,
    paddingLeft: spacing.xs,
    paddingRight: 5,
    borderRadius: 27,
    backgroundColor: colors.fill,
  },
  input: {
    ...type.body,
    flex: 1,
    color: colors.ink,
    maxHeight: 120,
    paddingTop: 11,
    paddingBottom: 11,
  },
  send: {
    width: TOUCH_TARGET,
    height: TOUCH_TARGET,
    borderRadius: radii.pill,
    backgroundColor: colors.ink,
    alignItems: "center",
    justifyContent: "center",
  },
  sendDisabled: { opacity: 0.25 },
});
