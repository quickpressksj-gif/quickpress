import 'package:flutter/material.dart';
import 'widgets/bottom_navbar.dart';

void main() {
  runApp(const QuickPressApp());
}

class QuickPressApp extends StatelessWidget {
  const QuickPressApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      title: 'QuickPress',
      theme: ThemeData(
        primaryColor: Colors.yellow,
        scaffoldBackgroundColor: Colors.white,
        useMaterial3: true,
      ),
      home: const BottomNavBar(),
    );
  }
}
