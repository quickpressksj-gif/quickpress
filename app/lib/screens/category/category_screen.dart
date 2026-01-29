import 'package:flutter/material.dart';

class CategoryScreen extends StatelessWidget {
  final String category;
  const CategoryScreen({super.key, required this.category});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text(category),
        backgroundColor: Colors.yellow,
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          productTile("Shirt Ironing", "₹10 / piece"),
          productTile("Pant Ironing", "₹12 / piece"),
          productTile("T-Shirt Ironing", "₹8 / piece"),
        ],
      ),
    );
  }

  Widget productTile(String name, String price) {
    return Card(
      child: ListTile(
        leading: const Icon(Icons.checkroom, color: Colors.orange),
        title: Text(name),
        subtitle: Text(price),
        trailing: ElevatedButton(
          style: ElevatedButton.styleFrom(backgroundColor: Colors.orange),
          onPressed: () {},
          child: const Text("ADD"),
        ),
      ),
    );
  }
}
