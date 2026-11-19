SetFactory("Built-in");

lc = 1.5;

Point(1) = {0, 0, 0, lc};
Point(2) = {40, 0, 0, lc};
Point(3) = {40, 5, 0, lc};
Point(4) = {0, 5, 0, lc};
Point(5) = {40, 45, 0, lc};
Point(6) = {0, 45, 0, lc};
Point(7) = {40, 50, 0, lc};
Point(8) = {0, 50, 0, lc};

Line(1) = {1, 2};
Line(2) = {2, 3};
Line(3) = {3, 4};
Line(4) = {4, 1};

Line(5) = {3, 5};
Line(6) = {5, 6};
Line(7) = {6, 4};

Line(8) = {5, 7};
Line(9) = {7, 8};
Line(10) = {8, 6};

Line Loop(101) = {1, 2, 3, 4};
Plane Surface(1) = {101};

Line Loop(102) = {-3, 5, 6, 7};
Plane Surface(2) = {102};

Line Loop(103) = {-6, 8, 9, 10};
Plane Surface(3) = {103};

Physical Surface("BottomElectrode") = {1};
Physical Surface("Electrolyte") = {2};
Physical Surface("TopElectrode") = {3};

Physical Line("BottomElectrodeBC") = {1};
Physical Line("TopElectrodeBC") = {9};

Mesh.CharacteristicLengthMin = 1;
Mesh.CharacteristicLengthMax = 2;
