#include <unity.h>

void setUp(void) {}
void tearDown(void) {}

void test_arithmetic_works(void) {
    TEST_ASSERT_EQUAL(4, 2 + 2);
}

void test_string_compare_works(void) {
    TEST_ASSERT_EQUAL_STRING("ginhawa", "ginhawa");
}

int main(int, char **) {
    UNITY_BEGIN();
    RUN_TEST(test_arithmetic_works);
    RUN_TEST(test_string_compare_works);
    return UNITY_END();
}
