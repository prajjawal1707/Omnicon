import unittest
from expense_manager import ExpenseManager

class TestExpenseManager(unittest.TestCase):
    def setUp(self):
        self.expense_manager = ExpenseManager()

    def test_add_expense(self):
        self.expense_manager.add_expense()
        self.assertIn(self.expense_manager.expenses, {})
        self.assertEqual(len(self.expense_manager.expenses), 1)

    def test_edit_expense(self):
        self.expense_manager.add_expense()
        self.expense_manager.edit_expense()
        self.assertIn(self.expense_manager.expenses, {})
        self.assertEqual(len(self.expense_manager.expenses), 1)

    def test_delete_expense(self):
        self.expense_manager.add_expense()
        self.expense_manager.delete_expense()
        self.assertNotIn(self.expense_manager.expenses, {})

    def test_monthly_expense_summary(self):
        self.expense_manager.add_expense()
        self.expense_manager.add_expense()
        self.expense_manager.monthly_expense_summary()
        self.assertGreaterEqual(self.expense_manager.expenses, 2)

    def test_category_wise_report(self):
        self.expense_manager.add_expense()
        self.expense_manager.add_expense()
        self.expense_manager.category_wise_report()
        self.assertGreaterEqual(len(self.expense_manager.expenses), 2)

if __name__ == "__main__":
    unittest.main()
