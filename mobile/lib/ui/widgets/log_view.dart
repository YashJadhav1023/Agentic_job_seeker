import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

enum LogLevel { error, warning, success, info }

/// Classifies one log line so the viewer can colour it. Pure function, unit tested.
LogLevel classifyLogLine(String line) {
  final l = line.toLowerCase();
  if (RegExp(r'\b(error|exception|traceback|failed|fatal|denied|refused|timed? ?out)\b').hasMatch(l)) {
    return LogLevel.error;
  }
  if (RegExp(r'\b(warn|warning|captcha|cooldown|restricted|skipp|retry|blocked)').hasMatch(l)) {
    return LogLevel.warning;
  }
  if (RegExp(r'\b(done|success|applied|sent|saved|ok|logged in|complete)').hasMatch(l)) {
    return LogLevel.success;
  }
  return LogLevel.info;
}

/// Terminal-style log viewer: colour by severity, search filter, copy-all and follow-the-tail toggle.
class LogView extends StatefulWidget {
  const LogView({super.key, required this.lines, this.follow = true});
  final List<String> lines;
  final bool follow;

  @override
  State<LogView> createState() => _LogViewState();
}

class _LogViewState extends State<LogView> {
  final _scroll = ScrollController();
  final _query = TextEditingController();
  bool _follow = true;
  bool _onlyProblems = false;

  @override
  void initState() {
    super.initState();
    _follow = widget.follow;
  }

  @override
  void didUpdateWidget(covariant LogView old) {
    super.didUpdateWidget(old);
    if (_follow && old.lines.length != widget.lines.length) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (_scroll.hasClients) _scroll.jumpTo(_scroll.position.maxScrollExtent);
      });
    }
  }

  @override
  void dispose() {
    _scroll.dispose();
    _query.dispose();
    super.dispose();
  }

  static Color _colour(LogLevel level) => switch (level) {
        LogLevel.error => const Color(0xFFFF8A80),
        LogLevel.warning => const Color(0xFFFFD180),
        LogLevel.success => const Color(0xFFB9F6CA),
        LogLevel.info => const Color(0xFFE0E0E0),
      };

  @override
  Widget build(BuildContext context) {
    final q = _query.text.trim().toLowerCase();
    final shown = <String>[
      for (final line in widget.lines)
        if ((q.isEmpty || line.toLowerCase().contains(q)) &&
            (!_onlyProblems || classifyLogLine(line) == LogLevel.error || classifyLogLine(line) == LogLevel.warning))
          line,
    ];
    final problems = widget.lines.where((l) {
      final c = classifyLogLine(l);
      return c == LogLevel.error || c == LogLevel.warning;
    }).length;
    return Column(children: [
      Padding(
        padding: const EdgeInsets.fromLTRB(12, 8, 4, 4),
        child: Row(children: [
          Expanded(
            child: TextField(
              controller: _query,
              onChanged: (_) => setState(() {}),
              decoration: InputDecoration(
                isDense: true,
                hintText: 'Search log',
                prefixIcon: const Icon(Icons.search, size: 18),
                suffixIcon: q.isEmpty
                    ? null
                    : IconButton(
                        icon: const Icon(Icons.clear, size: 18),
                        onPressed: () => setState(_query.clear),
                      ),
              ),
            ),
          ),
          IconButton(
            tooltip: 'Copy log',
            icon: const Icon(Icons.copy_all),
            onPressed: () {
              Clipboard.setData(ClipboardData(text: shown.join('\n')));
              ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Copied ${shown.length} lines')));
            },
          ),
        ]),
      ),
      Padding(
        padding: const EdgeInsets.symmetric(horizontal: 12),
        child: Wrap(spacing: 8, crossAxisAlignment: WrapCrossAlignment.center, children: [
          FilterChip(
            label: Text('Problems ($problems)'),
            selected: _onlyProblems,
            onSelected: (v) => setState(() => _onlyProblems = v),
          ),
          FilterChip(
            label: const Text('Follow'),
            selected: _follow,
            onSelected: (v) => setState(() => _follow = v),
          ),
          Text('${shown.length}/${widget.lines.length} lines', style: Theme.of(context).textTheme.bodySmall),
        ]),
      ),
      const SizedBox(height: 4),
      Expanded(
        child: Container(
          color: Colors.black,
          width: double.infinity,
          child: shown.isEmpty
              ? const Center(child: Text('No log lines', style: TextStyle(color: Colors.white54)))
              : ListView.builder(
                  controller: _scroll,
                  padding: const EdgeInsets.all(10),
                  itemCount: shown.length,
                  itemBuilder: (_, i) => SelectableText(
                    shown[i],
                    style: TextStyle(color: _colour(classifyLogLine(shown[i])), fontFamily: 'monospace', fontSize: 12),
                  ),
                ),
        ),
      ),
    ]);
  }
}
